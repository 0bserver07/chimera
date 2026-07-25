"""Chimera Multiplexer (Phase 2) — N agent lanes racing one task, side by side.

This is the comparison mission rendered as an interface: several coding agents
(different models / presets) attack the same task in their own isolated
workspaces, and you watch cost, tokens, steps, time, and outcome diverge in real
time. No mainstream terminal agent multiplexes — that is the differentiator.

Each lane is an independent :class:`~chimera.tui.lane.Lane` (its own
:class:`AgentDriver`, workspace, history, cost). The app is presentation only:
it renders each lane's ``LoopEvent`` stream into a pane and routes input to one
lane or all of them. Concurrency is real — the driver streams via async I/O, so
lanes overlap on Textual's event loop (see :mod:`chimera.tui.lane`).

Launch: ``chimera code --tui --models glm-5.2,glm-5.1`` (or ``chimera otter``).
Bare ``chimera code --tui`` runs the same app as a **one-lane multiplexer**
(:func:`run_single_agent`): an ``inplace`` lane editing the real tree, with the
single-lane chrome (no tabstrip, no pane border, an app-style status line).
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from dataclasses import replace
from typing import Any

try:
    from rich.text import Text
    from textual import on
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, OptionList, RichLog, Static, TextArea

    from chimera.tui.approvals import ApprovalBroker, ApprovalModal, approvals_enabled
    from chimera.tui.prompt import PromptArea, filter_commands
    from chimera.tui.select import FuzzySelectScreen, SelectItem
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The Chimera multiplexer needs the 'tui' extra:\n"
        "  pip install 'chimera-run[tui]'   (or: pip install textual)"
    ) from exc

from chimera.core.loop_events import LoopEventType
from chimera.tui.budget import (
    budget_from_dict,
    cohort_budget_from_config,
    cohort_terminal_reason,
    describe_budget,
    lane_budget_from_config,
    parse_budget_spec,
)
from chimera.tui.cohort import Cohort, load_cohort_retention, prune_cohorts
from chimera.tui.commands import completion_catalog, help_lines
from chimera.tui.keys import (
    KeymapError,
    apply_keymap,
    build_bindings,
    hidden_actions,
    key_for,
    keymap_table,
    load_user_keybinds,
    resolve_keymap,
)
from chimera.tui.lane import Lane, LaneConfig
from chimera.tui.live_region import LiveRegion
from chimera.tui.logview import TranscriptLog
from chimera.tui.render import LaneTranscript, heartbeat_line
from chimera.tui.routing import Action, RoutingMode, route
from chimera.tui.scrollback import inline_capability
from chimera.tui.statusline import (
    StatusContext,
    StatusLine,
    build_cohort_context,
    build_lane_context,
)
from chimera.tui.theme import Palette, ThemeSettings, load_theme_settings

__all__ = [
    "MultiplexApp",
    "LanePane",
    "ThemePickerScreen",
    "run_multiplexer",
    "run_single_agent",
    "resume_multiplexer",
    "print_saved_cohorts",
    "parse_lane_specs",
    "default_isolation",
]


def default_isolation(lane_count: int, explicit: str | None) -> str:
    """Resolve the workspace-isolation strategy for a cohort.

    An explicit user choice always wins. Otherwise a single lane runs
    ``inplace`` — a lone agent edits the real tree, daily-driver style, since
    isolation only protects lanes from *each other* — and 2+ lanes get
    ``auto`` (git worktree for a repo, else copy).
    """
    if explicit:
        return explicit
    return "inplace" if lane_count == 1 else "auto"


def _coerce_budget(value: Any) -> Any:
    """Normalize a budget argument to a :class:`BudgetSpec` or ``None``.

    Accepts a compact budget string (parsed via
    :func:`~chimera.tui.budget.parse_budget_spec`), an already-built
    :class:`~chimera.core.budget.BudgetSpec`, or ``None``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return parse_budget_spec(value)
    return value if getattr(value, "is_set", False) else None


def _resolve_budgets(
    project_dir: str | None,
    lane_budget: Any,
    cohort_budget: Any,
) -> tuple[Any, Any]:
    """Resolve the (lane-default, cohort) budgets from args + config (#170).

    An explicit argument (CLI ``--lane-budget`` / ``--budget`` or a programmatic
    :class:`BudgetSpec`) wins; otherwise the ``[tui.budget]`` and
    ``[tui.budget.cohort]`` tables supply defaults. Config discovery is
    best-effort — a broken config never blocks a launch. Both may be ``None``
    (the unbudgeted default).

    Returns:
        ``(lane_default_spec, cohort_spec)``.
    """
    lane_spec = _coerce_budget(lane_budget)
    cohort_spec = _coerce_budget(cohort_budget)
    if lane_spec is not None and cohort_spec is not None:
        return lane_spec, cohort_spec
    try:
        from chimera.config.user_config import load_tui_config

        tui = load_tui_config(project_dir)
    except Exception:  # noqa: BLE001 — config discovery must not block a launch
        tui = {}
    if lane_spec is None:
        lane_spec = lane_budget_from_config(tui)
    if cohort_spec is None:
        cohort_spec = cohort_budget_from_config(tui)
    return lane_spec, cohort_spec


# A pane narrower than this is unreadable; below it we degrade to tabs (§6.3).
MIN_PANE_WIDTH = 32
_HEADER_REFRESH_EVENTS = frozenset({
    LoopEventType.tool_use, LoopEventType.tool_result, LoopEventType.result,
})
# Which bindings exist per lane mode and which slash commands exist per
# surface both live in the registries now (chimera.tui.keys single_only /
# multi_only flags → hidden_actions(); chimera.tui.commands context field).


class LanePane(Vertical):
    """One lane rendered as a pane: a status header over a scrolling transcript.

    A pane is essentially a Phase-1 TUI reduced to a widget. It renders the
    lane's event stream via the shared :class:`LaneTranscript` so panes look
    identical to the single-agent TUI.
    """

    def __init__(
        self,
        lane: Lane,
        *,
        expand_hint: str = "",
        full_hint: str = "",
        palette: Palette | None = None,
        animations: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._lane = lane
        self._transcript: LaneTranscript | None = None
        self._live_region: LiveRegion | None = None
        self._live_frame = 0  # heartbeat animation tick, set by the app timer
        self._is_focus = False
        #: The currently bound expand-toggle key, injected by the app from its
        #: keybinding registry — elision markers advertise it (R-KEY-3).
        self._expand_hint = expand_hint
        #: The transcript-overlay key, advertised beside it (R-FOLD-7).
        self._full_hint = full_hint
        #: Semantic slot colors (R-THEME-1); ``None`` = the default theme.
        self._palette = palette
        #: R-THEME-4 motion gate: False freezes the heartbeat pulse.
        self._animations = animations

    def compose(self) -> ComposeResult:
        yield Static(self._header_text(), classes="lane-header")
        # TranscriptLog = RichLog + follow-mode (sticky tail with an escape
        # hatch) — a plain RichLog force-scrolls on every streamed event.
        yield TranscriptLog(classes="lane-log", wrap=True, markup=False, highlight=False)
        # Live region (R-REN-6 visible + R-FOLD-1): uncommitted tail +
        # thinking heartbeat, positionally fixed below the log — it keeps
        # updating even when the user has scrolled up. Ephemeral by contract
        # (R-VIEW-3): nothing it shows ever reaches a transcript sink.
        yield LiveRegion(classes="lane-live")

    def on_mount(self) -> None:
        self._transcript = LaneTranscript(
            self.query_one(RichLog).write,
            expand_hint=self._expand_hint,
            full_hint=self._full_hint,
            palette=self._palette,
        )
        self._live_region = self.query_one(LiveRegion)

    # -- rendering ------------------------------------------------------
    def _header_text(self) -> Text:
        t = self._lane.telemetry
        marker = "▸ " if self._is_focus else "  "
        reason = t.terminal_reason
        state = t.liveness.value
        if reason and reason not in ("completed", None):
            state = f"{state}:{reason}"
        return Text(
            f"{marker}{self._lane.label} · {self._lane.config.model} · "
            f"${t.cost:.4f} · {state} · {t.steps} st",
            style="bold" if self._is_focus else "",
        )

    def refresh_header(self) -> None:
        # Teardown-safe (same posture as _refresh_global): a turn cancelled at
        # app shutdown — e.g. quitting while it waits on an approval modal
        # (#171) — runs _drive's finally after the pane's children unmounted.
        nodes = self.query(".lane-header")
        if nodes:
            nodes.first(Static).update(self._header_text())

    def feed(self, ev: Any) -> None:
        if self._transcript is not None:
            self._transcript.handle(ev)
            self._refresh_live()

    def commit(self) -> None:
        if self._transcript is not None:
            self._transcript.commit()
            self._refresh_live()  # tail flushed, thinking committed → hides

    def _refresh_live(self) -> None:
        """Sync the live region to the transcript's uncommitted state."""
        if self._transcript is None or self._live_region is None:
            return
        hb = ""
        if self._transcript.thinking_active:
            hb = heartbeat_line(
                self._transcript.thinking_elapsed,
                self._transcript.thinking_chars,
                self._live_frame,
                animate=self._animations,
            )
        self._live_region.show(
            tail=self._transcript.live_tail,
            heartbeat=hb,
            markdown=self._transcript.markdown,
        )

    def pulse_live(self, frame: int) -> None:
        """App-timer tick: advance the heartbeat animation (no pane timers)."""
        self._live_frame = frame
        if self._transcript is not None and self._transcript.thinking_active:
            self._refresh_live()

    def clear_live(self) -> None:
        """Drop any live-region content immediately (lane clear)."""
        if self._live_region is not None:
            self._live_region.clear_live()

    def echo_user(self, text: str) -> None:
        # The user's own input always re-pins the tail (terminal convention),
        # even if they had scrolled up to read.
        log = self.query_one(TranscriptLog)
        log.write(Text.assemble(("› ", "bold cyan"), (text, "bold")))
        log.jump_to_tail()

    def note(self, text: str, style: str = "magenta", *, follow: bool = False) -> None:
        log = self.query_one(TranscriptLog)
        log.write(Text(text, style=style))
        if follow:
            log.jump_to_tail()

    def feed_error(self, exc: object) -> None:
        self.query_one(RichLog).write(Text(f"turn failed: {exc}", style="red"))

    def intro(self, *, single: bool = False) -> None:
        if single:
            # The daily-driver greeting (ported from the retired single-agent
            # app): model + context window + how to get help / cancel.
            c = getattr(self._lane.driver, "context_window", None)
            ctx = f"{c:,}" if c else "?"
            self.query_one(RichLog).write(Text(
                f"Chimera TUI — {self._lane.config.model} — "
                f"{self._lane.config.preset} — {ctx} ctx.  "
                f"/help for commands · Ctrl+C cancels a turn.",
                style="dim",
            ))
            return
        ws = self._lane.workspace
        where = f" · {ws.strategy}" if ws else ""
        self.query_one(RichLog).write(Text(
            f"{self._lane.label} — {self._lane.config.model} — {self._lane.config.preset}{where}",
            style="dim",
        ))

    def set_focused(self, value: bool) -> None:
        self._is_focus = value
        self.set_class(value, "focused-lane")
        self.refresh_header()

    def set_show_reasoning(self, value: bool) -> None:
        if self._transcript is not None:
            self._transcript.show_reasoning = value

    def set_elide(self, value: bool) -> None:
        """Collapse (True) or expand (False) tool output — display-only
        (R-FOLD-2/3). Takes effect for tool results rendered from now on;
        already-committed output re-renders with the transcript overlay
        (R-FOLD-7, a later wave)."""
        if self._transcript is not None:
            self._transcript.elide = value

    def reveal_reasoning(self) -> bool:
        return self._transcript.reveal_last() if self._transcript is not None else False

    def set_palette(self, palette: Palette | None) -> None:
        """Repaint with a new theme palette (R-THEME-3 live preview).

        Forward-looking, like the expand toggle: the pane's sink is
        append-only, so already-committed renderables keep the styles they were
        written with; everything rendered from now on uses *palette*.
        """
        self._palette = palette
        if self._transcript is not None:
            self._transcript.palette = palette


class MultiplexApp(App):
    """Full-screen host for N lanes racing one task (spec §6)."""

    TITLE = "Chimera Multiplexer"

    CSS = """
    Screen { layers: base; }
    #global-status { height: 1; background: $primary; color: $text; padding: 0 1; }
    #summary { height: auto; max-height: 4; background: $panel; color: $text; padding: 0 1; }
    #tabstrip { height: 1; background: $panel; color: $text; padding: 0 1; }
    #lanes { height: 1fr; }
    .lane-pane { width: 1fr; border: round $secondary; }
    .lane-pane.focused-lane { border: round $accent; }
    /* Single-lane mode: one full-width pane, so per-lane chrome is noise. */
    .lane-pane.single { border: none; }
    .lane-pane.single.focused-lane { border: none; }
    .lane-pane.single .lane-header { display: none; }
    .lane-header { height: 1; background: $boost; padding: 0 1; }
    .lane-log { height: 1fr; padding: 0 1; }
    #sidebar { width: 32; border: round $secondary; padding: 0 1; }
    #prompt { border: round $secondary; height: auto; max-height: 8; }
    #hint { height: 1; color: $text-muted; padding: 0 1; }
    """

    #: Default bindings come from the declarative action registry (R-KEY-1);
    #: per-key notes (priority flags, single/multi gating) live there. User
    #: overrides from ``tui.keybinds`` are applied per instance in __init__.
    BINDINGS = build_bindings()

    #: Local slash-command catalog (drives /help and slash autocomplete),
    #: derived from the slash-command registry (chimera.tui.commands).
    #: NOT named ``COMMANDS`` — that shadows Textual's command-palette provider
    #: registry on ``App``, and the palette then crashes on Ctrl+P trying to
    #: instantiate strings as providers.
    SLASH_COMMANDS = completion_catalog()

    def __init__(
        self,
        cohort: Cohort,
        *,
        lane_cap: int | None = None,
        initial_task: str | None = None,
        persist_root: str | None = None,
        keybinds: dict[str, Any] | None = None,
        approval_broker: Any | None = None,
        theme_settings: ThemeSettings | None = None,
        **kwargs: Any,
    ) -> None:
        #: R-THEME-1..4: resolved theme settings + the live palette. Injectable
        #: for tests; by default read from the same config chain as keybinds
        #: and the status line. Discovery is best-effort — a broken theme file
        #: degrades to the default theme (byte-identical to pre-theme output).
        #: Resolved BEFORE ``super().__init__``: the framework builds its
        #: stylesheet from ``get_css_variables()`` inside App's constructor.
        self._theme_settings = (
            theme_settings if theme_settings is not None
            else load_theme_settings(cohort.source or None)
        )
        self._palette = self._theme_settings.palette()
        #: Preview state for the ``/theme`` picker: the palette to restore when
        #: the picker is cancelled (R-THEME-3).
        self._theme_restore: Palette | None = None
        super().__init__(**kwargs)
        self._cohort = cohort
        # -- permission approvals (#171): set only when the opt-in is on ----
        self._approval_broker = approval_broker
        self._active_approval: Any | None = None
        self._mode = cohort.routing
        #: One lane = the daily-driver single-agent TUI (issue #172): same app,
        #: with the multi-lane chrome (tabstrip, pane border/header, routing
        #: modes) degraded away. See ``check_action`` and ``_relayout``.
        self._single = len(cohort.lanes) == 1
        if self._single:
            self.title = "Chimera TUI"
        #: R-KEY-2: user rebinding. ``keybinds`` is injectable for tests; by
        #: default the ``tui.keybinds`` table comes from the config chain. An
        #: invalid table must not kill the app — fall back to defaults and
        #: surface the (loud, both-actions-named) error once mounted.
        #: (Named ``_keybinds``: Textual's App owns ``_keymap`` for its own
        #: ``set_keymap`` feature — shadowing it corrupts binding refresh.)
        self._keybinds_error: str | None = None
        try:
            self._keybinds = resolve_keymap(
                load_user_keybinds() if keybinds is None else keybinds
            )
        except KeymapError as exc:
            self._keybinds = resolve_keymap({})
            self._keybinds_error = str(exc)
        apply_keymap(self._bindings, self._keybinds)
        self._slash_commands = completion_catalog(single=self._single)
        self._focus_index = 0
        self._panes: list[LanePane] = []
        self._pane_by_id: dict[str, LanePane] = {}
        self._tabbed = False
        self._lane_cap = lane_cap
        self._initial_task = initial_task
        self._persist_root = persist_root
        self._race_start: float | None = None
        self._race_end: float | None = None
        self._completion_order = 0
        #: Cohort budget (#170): lane ids cancelled by the aggregate cap, mapped
        #: to their honest ``cohort_budget:<dim>`` terminal reason. Applied when
        #: the cancelled turn ends, so the reason wins over the generic abort.
        self._cohort_cancelled: dict[str, str] = {}
        self._slots: Any = None  # asyncio.Semaphore, created on mount
        self._show_reasoning = False
        self._tools_expanded = False  # R-FOLD-2 global expand toggle state
        self._sidebar_on = False
        self._live_frame = 0  # one app-level heartbeat clock for all panes
        self._live_timer: Any = None
        #: Status line + terminal title (R-STAT-1..5): configured item order,
        #: async git facts, OSC title guard — all owned by StatusLine.
        self._statusline = StatusLine(cohort.source or None, single=self._single)
        self._status_ctx = StatusContext()
        #: Set by /resume (or the /cohorts picker) just before exit; the outer
        #: run loop reads it and relaunches on the requested saved cohort.
        self.resume_request: str | None = None

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(self._global_status_text(), id="global-status")
        yield Static("", id="summary")
        yield Static("", id="tabstrip")
        pane_classes = "lane-pane single" if self._single else "lane-pane"
        expand_hint = key_for("toggle_expand", self._keybinds)
        # R-FOLD-7: every elision marker names the *currently bound* key that
        # opens the untruncated transcript overlay (R-KEY-3 — empty when the
        # user unbound it, and then simply not advertised).
        full_hint = key_for("show_transcript", self._keybinds)
        with Horizontal(id="lanes"):
            for lane in self._cohort.lanes:
                pane = LanePane(
                    lane,
                    classes=pane_classes,
                    expand_hint=expand_hint,
                    full_hint=full_hint,
                    palette=self._palette,
                    animations=self._theme_settings.animations,
                )
                self._panes.append(pane)
                self._pane_by_id[lane.id] = pane
                yield pane
            yield Static("", id="sidebar")
        yield Static("", id="hint")
        yield PromptArea(placeholder=self._placeholder(), commands=self._slash_commands, id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        import asyncio

        self._slots = asyncio.Semaphore(self._lane_cap or len(self._cohort.lanes))
        self.query_one("#prompt", PromptArea).focus()
        self.query_one("#summary", Static).display = False
        self.query_one("#hint", Static).display = False
        self._update_focus_styles()
        self._relayout()
        for pane in self._panes:
            pane.intro(single=self._single)
        if self._keybinds_error:
            self._say_focused(
                f"tui.keybinds ignored (using defaults): {self._keybinds_error}",
                style="red",
            )
        if self._theme_settings.error:
            self._say_focused(
                f"tui theme config: {self._theme_settings.error}", style="red",
            )
        self._statusline.start()
        self.set_interval(0.5, self._refresh_global)
        # ONE interval animates every pane's heartbeat (R-FOLD-1): per-pane
        # timers would drift out of phase and multiply wakeups. With animations
        # off (R-THEME-4) the pulse is static, so the tick only has to keep the
        # elapsed/size figures honest — a quarter of the wakeups does that.
        self._live_timer = self.set_interval(
            0.25 if self._theme_settings.animations else 1.0, self._pulse_live,
        )
        # -- permission approvals (#171): drain the broker queue as modals --
        if self._approval_broker is not None:
            self.set_interval(0.15, self._pump_approvals)
        if self._initial_task:
            self._submit_text(self._initial_task)

    def on_unmount(self) -> None:
        # Clean shutdown (R-STAT-3/5): stop the git watcher thread and
        # restore the terminal title we replaced.
        self._statusline.stop()

    def on_resize(self) -> None:
        self._relayout()

    # -- themes (R-THEME-1..4) -------------------------------------------
    def get_css_variables(self) -> dict[str, str]:
        """Merge the theme's slot colors into the framework's design tokens.

        Only hex-valued slots are exported (see
        :meth:`chimera.tui.theme.Palette.css_variables`), so a terminal-palette
        theme — including the default — leaves framework chrome exactly as
        shipped and this override is a no-op.
        """
        # Annotated so mypy keeps a concrete dict under CI's no-textual posture
        # (the framework resolves to Any there — a bare call returns Any).
        variables: dict[str, str] = dict(super().get_css_variables())
        variables.update(self._palette.css_variables())
        return variables

    def apply_palette(self, palette: Palette) -> None:
        """Adopt *palette* live: panes repaint, framework chrome re-resolves.

        Used by the ``/theme`` picker for preview and for the final choice
        (R-THEME-3). Already-committed transcript renderables keep their
        original styles — the sinks are append-only — so the switch shows up
        on everything rendered from here on.
        """
        self._palette = palette
        for pane in self._panes:
            pane.set_palette(palette)
        try:
            self.refresh_css(animate=False)
        except Exception:  # noqa: BLE001 - a repaint failure must not kill a turn
            pass

    def _open_theme_picker(self) -> None:
        """``/theme``: pick a theme with live preview and restore-on-cancel."""
        settings = self._theme_settings
        names = sorted(settings.themes) or [self._palette.name]
        self._theme_restore = self._palette

        def _preview(name: str) -> None:
            self.apply_palette(settings.palette(name))

        def _chosen(name: Any) -> None:
            if name:
                self._theme_settings = replace(settings, theme=str(name))
                self.apply_palette(settings.palette(str(name)))
                self._say_focused(
                    f"theme: {name} ({settings.mode}) — persist it with "
                    f'[tui] theme = "{name}"', style="green",
                )
            elif self._theme_restore is not None:
                self.apply_palette(self._theme_restore)  # cancel restores
            self._theme_restore = None

        self.push_screen(
            ThemePickerScreen(
                [settings.themes[n] for n in names if n in settings.themes],
                current=self._palette.name,
                mode=settings.mode,
                depth=settings.depth,
                on_preview=_preview,
            ),
            _chosen,
        )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Gate bindings by lane count (dynamic actions).

        Single-lane mode hides the multi-lane chrome keys (focus-prev,
        broadcast toggle, per-lane cancel/clear) and enables Ctrl+L "Clear";
        multi-lane mode is the mirror image. The mode sets come from the
        action registry's ``single_only``/``multi_only`` flags. Slash
        commands are unaffected — they dispatch directly, not through
        actions.
        """
        if action in hidden_actions(self._single):
            return False
        return True

    def _relayout(self) -> None:
        n = len(self._panes)
        width = self.size.width or 80
        # One full-width lane never degrades to tabs — the strip would only
        # repeat the single label the status line already shows.
        self._tabbed = (not self._single) and (width // max(n, 1)) < MIN_PANE_WIDTH
        for i, pane in enumerate(self._panes):
            pane.display = (not self._tabbed) or (i == self._focus_index)
        strip = self.query_one("#tabstrip", Static)
        strip.display = self._tabbed
        if self._tabbed:
            strip.update(self._tabstrip_text())
        # Sidebar (§13.7): only when toggled on AND the terminal is wide enough
        # to spare its column (auto-hide, §5.11).
        sidebar = self.query_one("#sidebar", Static)
        sidebar.display = self._sidebar_on and not self._tabbed and width >= 100
        if sidebar.display:
            self._refresh_sidebar()

    def _tabstrip_text(self) -> Text:
        parts: list[tuple[str, str]] = []
        for i, lane in enumerate(self._cohort.lanes):
            style = "bold reverse" if i == self._focus_index else "dim"
            parts.append((f" {lane.label} ", style))
            parts.append((" ", ""))
        return Text.assemble("tabs: ", *parts)

    # -- status ---------------------------------------------------------
    def _placeholder(self) -> str:
        if self._single:
            return "Ask, or /help …"
        if self._mode is RoutingMode.BROADCAST:
            return "Broadcast to all lanes, or /help …"
        lane = self._focused_lane()
        return f"Target @{lane.label if lane else '?'}, or /help …"

    def _global_status_text(self) -> Text:
        """The status line via the item registry (R-STAT-1/2/4).

        Multi-lane renders cohort aggregates; content and order come from
        ``tui.status_line`` (default: the racing scoreboard), degraded per
        segment to the terminal width so the line never wraps.
        """
        if self._single:
            return self._single_status_text()
        racing = self._race_start is not None
        self._status_ctx = build_cohort_context(
            self._cohort,
            mode=self._mode.value,
            elapsed=self._elapsed() if racing else None,
            racing=racing,
            git=self._statusline.git_facts(),
        )
        return self._statusline.render(self._status_ctx, self._status_width())

    def _single_status_text(self) -> Text:
        """The daily-driver status line, same registry, lane-scoped context.

        Default order (spec §11): model · context-used · cost · run-state;
        lane/race counters would be noise for one lane.
        """
        self._status_ctx = build_lane_context(
            self._cohort.lanes[0], git=self._statusline.git_facts(),
        )
        return self._statusline.render(self._status_ctx, self._status_width())

    def _status_width(self) -> int:
        # The #global-status Static has `padding: 0 1` — one column each side.
        return max(16, (self.size.width or 80) - 2)

    def _elapsed(self) -> float:
        if self._race_start is None:
            return 0.0
        end = self._race_end if self._race_end is not None else time.monotonic()
        return end - self._race_start

    def _refresh_global(self) -> None:
        # Cohort budget (#170): enforce before rendering so a trip is reflected
        # the same tick. A no-op when no cohort budget is set or before a race.
        self._check_cohort_budget()
        # Interval-timer callback: it can race app teardown (the widget tree is
        # already unmounting), so a missing node is a no-op, not an error.
        nodes = self.query("#global-status")
        if nodes:
            nodes.first(Static).update(self._global_status_text())
            # Terminal title mirrors the same context (R-STAT-5).
            self._statusline.apply_title(self._status_ctx, app=self)

    def _pulse_live(self) -> None:
        # The single app-level heartbeat tick; panes not thinking no-op.
        self._live_frame += 1
        for pane in self._panes:
            pane.pulse_live(self._live_frame)

    def _show_summary(self) -> None:
        rows = self._cohort.summary_rows()
        parts: list[str] = []
        for r in rows:
            tag = {"done": "✓", "error": "✗"}.get(r["liveness"], "·")
            order = f"#{r['finished_order']}" if r["finished_order"] else "—"
            extra = ""
            if r["terminal_reason"] and r["terminal_reason"] not in ("completed", None):
                extra = f",{r['terminal_reason']}"
            parts.append(
                f"{tag} {r['label']}({order},${r['cost']:.4f},{r['steps']}st,"
                f"{r['elapsed']:.1f}s{extra})"
            )
        # Teardown-safe: the last turn's finally can reach here while the app
        # is unmounting (see refresh_header) — a missing node is a no-op.
        nodes = self.query("#summary")
        if not nodes:
            return
        summary = nodes.first(Static)
        summary.update(Text(
            "cohort · " + "   ".join(parts) + "   ·   Ctrl+R: compare outputs",
            style="bold",
        ))
        summary.display = True

    # -- focus & lanes --------------------------------------------------
    def _focused_lane(self) -> Lane | None:
        if not self._cohort.lanes:
            return None
        return self._cohort.lanes[self._focus_index]

    def _focused_lane_id(self) -> str | None:
        lane = self._focused_lane()
        return lane.id if lane else None

    def _pane(self, lane_id: str) -> LanePane:
        return self._pane_by_id[lane_id]

    def _update_focus_styles(self) -> None:
        for i, pane in enumerate(self._panes):
            pane.set_focused(i == self._focus_index)

    # -- input ----------------------------------------------------------
    @on(PromptArea.Submitted, "#prompt")
    def _on_input(self, event: PromptArea.Submitted) -> None:
        # ``value`` is what gets sent (paste chips expanded, R-FOLD-6);
        # ``raw`` is what was on screen — history recalls the chip, not the
        # wall of text it stands for. They are the same string when nothing
        # was collapsed.
        text = event.value.strip()
        event.prompt.remember(event.raw)
        event.prompt.value = ""
        self._hide_hint()
        self._submit_text(text)

    @on(TextArea.Changed, "#prompt")
    def _on_prompt_changed(self, event: TextArea.Changed) -> None:
        # Autocomplete hint (§13.6): show matching commands while a "/" prefix
        # is being typed; Tab completes.
        matches = filter_commands(event.text_area.text, self._slash_commands)
        hint = self.query_one("#hint", Static)
        if matches:
            hint.update(Text("  ".join(matches) + "   (Tab completes)", style="dim"))
            hint.display = True
        else:
            hint.display = False

    def _hide_hint(self) -> None:
        self.query_one("#hint", Static).display = False

    def _submit_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        lanes_state = [(lane.id, lane.telemetry.busy) for lane in self._cohort.lanes]
        actions = route(text, self._mode, lanes_state, self._focused_lane_id())
        if actions and actions[0].lane_id == "*":
            self._handle_command(text)
            return

        starting = [self._cohort.lane(a.lane_id) for a in actions if a.action is Action.NEW_TURN]
        starting_lanes = [lane for lane in starting if lane is not None]
        if starting_lanes:
            self._begin_race(starting_lanes)

        for a in actions:
            lane = self._cohort.lane(a.lane_id)
            if lane is None:
                continue
            pane = self._pane(lane.id)
            if a.action is Action.NEW_TURN:
                pane.echo_user(text)
                lane.note(f"› {text}")
                self._start_turn(lane, text)
            elif a.action is Action.STEER:
                lane.driver.steer(text)
                pane.note(f"↳ steer: {text}", follow=True)
                lane.note(f"↳ steer: {text}")
            elif a.action is Action.FOLLOW_UP:
                lane.driver.queue_follow_up(text)
                pane.note(f"↳ queued: {text}", style="cyan", follow=True)
        self._refresh_global()

    def _begin_race(self, lanes: list[Lane]) -> None:
        self._race_start = time.monotonic()
        self._race_end = None
        self._completion_order = 0
        for lane in self._cohort.lanes:
            lane.telemetry.finished_order = None
        for lane in lanes:
            lane.reset_race()
        self.query_one("#summary", Static).display = False

    def _start_turn(self, lane: Lane, text: str) -> None:
        lane.mark_queued()
        self._pane(lane.id).refresh_header()
        self.run_worker(
            self._drive(lane, text),
            group=f"lane-{lane.id}",
            name=f"drive-{lane.id}",
            exclusive=True,
        )

    async def _drive(self, lane: Lane, text: str) -> None:
        pane = self._pane(lane.id)
        assert self._slots is not None
        async with self._slots:
            lane.on_turn_begin()
            pane.refresh_header()
            try:
                async for ev in lane.driver.send(text):
                    lane.record(ev)
                    pane.feed(ev)
                    if ev.type in _HEADER_REFRESH_EVENTS:
                        pane.refresh_header()
                        if self._sidebar_on and lane is self._focused_lane():
                            self._refresh_sidebar()
            except Exception as exc:  # noqa: BLE001 - surfaced to the pane
                lane.telemetry.terminal_reason = "error"
                pane.feed_error(exc)
            finally:
                pane.commit()
                self._completion_order += 1
                lane.on_turn_end(order=self._completion_order)
                # Cohort budget (#170): a lane cancelled by the aggregate cap
                # reports the honest cohort reason, not the generic abort.
                if lane.id in self._cohort_cancelled:
                    lane.telemetry.terminal_reason = self._cohort_cancelled[lane.id]
                pane.refresh_header()
                self._refresh_global()
                if self._cohort.all_done:
                    self._finish_race()

    def _finish_race(self) -> None:
        if self._race_start is not None and self._race_end is None:
            self._race_end = time.monotonic()
        # One lane has nothing to rank: the transcript's own result line and
        # the status bar already carry cost/steps. /summary still works.
        if not self._single:
            self._show_summary()

    def _check_cohort_budget(self) -> None:
        """Cancel still-running lanes when the cohort aggregate cap trips (#170).

        Aggregates total $ and total steps across lanes plus the race's real
        elapsed time, judges them against the cohort
        :class:`~chimera.core.budget.BudgetSpec` (reusing its
        :meth:`~chimera.core.budget.BudgetSpec.first_exhausted`), and on a hit
        cooperatively cancels every busy lane — SIGTERM-style, the existing
        ``driver.cancel()`` path, never a kill. The honest
        ``cohort_budget:<dim>`` reason is recorded now and stamped onto each
        lane's telemetry when its cancelled turn ends (see :meth:`_drive`), so it
        wins over the generic abort reason. Idempotent: a lane already flagged is
        skipped, so this is safe to call on every refresh tick.
        """
        spec = getattr(self._cohort, "budget", None)
        if spec is None or self._race_start is None:
            return
        from chimera.core.budget import BudgetTally

        tally = BudgetTally(
            cost_usd=self._cohort.total_cost,
            llm_calls=self._cohort.total_steps,
            accumulated_sec=self._elapsed(),
        )
        hit = spec.first_exhausted(tally)
        if hit is None:
            return
        reason = cohort_terminal_reason(hit[0])
        for lane in self._cohort.lanes:
            if lane.telemetry.busy and lane.id not in self._cohort_cancelled:
                self._cohort_cancelled[lane.id] = reason
                lane.driver.cancel()
                pane = self._pane_by_id.get(lane.id)
                if pane is not None:
                    pane.note(f"· cohort budget hit ({reason})", style="red")

    # -- actions --------------------------------------------------------
    def action_cancel_all(self) -> None:
        running = [lane for lane in self._cohort.lanes if lane.telemetry.busy]
        if running:
            for lane in running:
                lane.driver.cancel()
                self._pane(lane.id).note("· cancel requested", style="red")
        else:
            self.exit()

    def action_cancel_focused(self) -> None:
        lane = self._focused_lane()
        if lane and lane.telemetry.busy:
            lane.driver.cancel()
            self._pane(lane.id).note("· cancel requested", style="red")

    def action_copy_selection(self) -> None:
        """Copy the transcript's current text selection to the clipboard (OSC 52).

        Textual's own selection machinery highlights the drag; this commits it
        to the system clipboard via ``App.copy_to_clipboard`` (OSC 52, so it
        also lands on the *local* clipboard over SSH). Ctrl+C stays cancel.
        """
        text = self.screen.get_selected_text() if self.screen is not None else None
        if not text:
            self.notify("Nothing selected — drag to select first", severity="warning", timeout=2.0)
            return
        self.copy_to_clipboard(text)
        self.notify(f"Copied {len(text):,} chars to clipboard", timeout=2.0)

    def action_focus_next_lane(self) -> None:
        if self._panes:
            self._focus_index = (self._focus_index + 1) % len(self._panes)
            self._update_focus_styles()
            self._relayout()
            self.query_one("#prompt", PromptArea).placeholder = self._placeholder()

    def action_focus_prev_lane(self) -> None:
        if self._panes:
            self._focus_index = (self._focus_index - 1) % len(self._panes)
            self._update_focus_styles()
            self._relayout()
            self.query_one("#prompt", PromptArea).placeholder = self._placeholder()

    def action_toggle_broadcast(self) -> None:
        self._mode = (
            RoutingMode.TARGETED if self._mode is RoutingMode.BROADCAST else RoutingMode.BROADCAST
        )
        self.query_one("#prompt", PromptArea).placeholder = self._placeholder()
        self._refresh_global()

    def action_clear_focused(self) -> None:
        lane = self._focused_lane()
        if lane is None:
            return
        lane.driver.clear()
        pane = self._pane(lane.id)
        pane.query_one(RichLog).clear()
        pane.clear_live()
        pane.note("(conversation cleared)", style="dim")

    def action_clear_lane(self) -> None:
        """Ctrl+L, the single-lane "Clear" key (app-parity alias)."""
        self.action_clear_focused()

    def action_show_results(self) -> None:
        ran = any(
            lane.telemetry.turns > 0 or lane.telemetry.busy for lane in self._cohort.lanes
        )
        if not ran:
            lane = self._focused_lane()
            if lane is not None:
                self._pane(lane.id).note("no results yet — run a task first", style="dim")
            return
        from chimera.tui.results import ResultsScreen

        self.push_screen(ResultsScreen(self._cohort, palette=self._palette))

    def action_show_transcript(self) -> None:
        """R-FOLD-7: open the focused lane's full, untruncated transcript.

        The panes elide tool output for display only — the lane's record kept
        everything (R-FOLD-3), and this overlay is where it is read. Ignored
        while another overlay is on the stack (a picker/approval modal owns
        the screen; the same key would otherwise stack a second pager over it).
        """
        if len(self.screen_stack) > 1:
            return
        lane = self._focused_lane()
        if lane is None:
            return
        from chimera.tui.transcript_view import TranscriptScreen

        self.push_screen(TranscriptScreen(
            lane, palette=self._palette, keymap=self._keybinds,
        ))

    def action_smart_tab(self) -> None:
        """Tab: complete a "/" command being typed, else cycle lane focus."""
        prompt = self.query_one("#prompt", PromptArea)
        if prompt.has_focus and prompt.text.lstrip().startswith("/"):
            from chimera.tui.prompt import complete_command

            completed = complete_command(prompt.text, self._slash_commands)
            if completed != prompt.text:
                prompt.text = completed
                prompt.move_cursor(prompt.document.end)
            return
        self.action_focus_next_lane()

    def action_toggle_reasoning(self) -> None:
        """Show/hide reasoning blocks (§13.4); revealing shows the focused
        lane's most recent hidden block."""
        self._show_reasoning = not self._show_reasoning
        for pane in self._panes:
            pane.set_show_reasoning(self._show_reasoning)
        lane = self._focused_lane()
        if lane is not None:
            pane = self._pane(lane.id)
            if self._show_reasoning:
                if not pane.reveal_reasoning():
                    pane.note("reasoning: shown (none captured yet)", style="dim")
            else:
                pane.note("reasoning: hidden", style="dim")

    def action_toggle_expand(self) -> None:
        """R-FOLD-2: the global expand toggle — flip tool-output elision
        everywhere.

        Display-only (the session record always keeps full output, R-FOLD-3)
        and forward-looking: it changes how tool results render from now on.
        The panes' sinks are append-only, so already-committed output is read
        untruncated in the transcript overlay instead
        (:meth:`action_show_transcript`, R-FOLD-7).
        """
        self._tools_expanded = not self._tools_expanded
        for pane in self._panes:
            pane.set_elide(not self._tools_expanded)
        state = "expanded" if self._tools_expanded else "collapsed"
        self._say_focused(f"tool output: {state} (applies to new output)")

    def action_toggle_sidebar(self) -> None:
        self._sidebar_on = not self._sidebar_on
        self._relayout()

    def _refresh_sidebar(self) -> None:
        lane = self._focused_lane()
        sidebar = self.query_one("#sidebar", Static)
        if lane is None:
            sidebar.update("")
            return
        rows: list[str] = [f"⚙ {lane.label} — tool calls"]
        for name, ok in lane.tool_log[-30:]:
            mark = "…" if ok is None else ("✓" if ok else "✗")
            rows.append(f" {mark} {name}")
        if len(lane.tool_log) > 30:
            rows.insert(1, f" … ({len(lane.tool_log) - 30} earlier)")
        if not lane.tool_log:
            rows.append(" (none yet)")
        sidebar.update(Text("\n".join(rows), style="dim"))

    # -- slash commands (frontend-local) --------------------------------
    def _handle_command(self, text: str) -> None:
        cmd = text.split()[0]
        lane = self._focused_lane()
        pane = self._pane(lane.id) if lane else None

        def say(msg: str, style: str = "dim") -> None:
            if pane is not None:
                pane.note(msg, style=style)

        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/help":
            # Generated from the two registries: the slash-command catalog
            # and the CURRENTLY-BOUND keys (true after rebinding, R-KEY-3).
            for line in help_lines(single=self._single, keymap=self._keybinds):
                say(line)
        elif cmd == "/keys":
            say("\n".join(keymap_table(self._keybinds)))
        elif cmd == "/model":
            if self._single and lane is not None:
                # App-parity: the one model plus its context window.
                c = getattr(lane.driver, "context_window", None)
                ctx = f"{c:,}" if c else "?"
                say(f"{lane.config.model}  ({ctx} ctx)")
            else:
                say("  ".join(f"{ln.label}={ln.config.model}" for ln in self._cohort.lanes))
        elif cmd == "/cost":
            if self._single:
                say(f"cumulative: ${self._cohort.total_cost:.4f}")
            else:
                say(f"Σ ${self._cohort.total_cost:.4f}  ·  " + "  ".join(
                    f"{ln.label}=${ln.telemetry.cost:.4f}" for ln in self._cohort.lanes
                ))
        elif cmd == "/budget":
            self._handle_budget_command(text)
        elif cmd == "/tools":
            if lane is not None:
                say(", ".join(t.name for t in lane.driver.tools) or "(none)")
        elif cmd == "/clear":
            self.action_clear_focused()
        elif cmd == "/statusline":
            # LIST view (R-STAT-1): id, order slot, availability. The
            # interactive reorder picker arrives with the shared select-dialog
            # component; StatusLine.describe() is the seam it will consume.
            self._global_status_text()  # refresh the context snapshot
            for line in self._statusline.describe(self._status_ctx):
                say(line)
        elif cmd == "/summary":
            self._show_summary()
        elif cmd == "/results":
            self.action_show_results()
        elif cmd == "/export":
            try:
                out = self._cohort.persist(root=self._persist_root)
                say(f"saved: {out}", style="green")
            except Exception as exc:  # noqa: BLE001
                say(f"export failed: {exc}", style="red")
        elif cmd in ("/broadcast", "/target"):
            self._mode = RoutingMode.BROADCAST if cmd == "/broadcast" else RoutingMode.TARGETED
            self.query_one("#prompt", PromptArea).placeholder = self._placeholder()
            self._refresh_global()
        elif cmd == "/theme":
            self._handle_theme_command(text)
        elif cmd == "/cohorts":
            self._open_cohort_picker()
        elif cmd == "/resume":
            parts = text.split()
            if len(parts) > 1:
                self.request_resume(parts[1])
            else:
                self._open_cohort_picker()
        else:
            say(f"unknown command: {cmd}", style="red")

    # -- themes (#R-THEME-3) ---------------------------------------------
    def _handle_theme_command(self, text: str) -> None:
        """``/theme`` — open the picker, list themes, or switch directly.

        ``/theme`` opens the fuzzy picker (live preview, Esc restores);
        ``/theme list`` prints the catalog with the active mode and depth;
        ``/theme <name>`` switches immediately. The choice is session-scoped —
        the message names the config key that makes it permanent.
        """
        arg = text[len("/theme"):].strip()
        settings = self._theme_settings
        if not arg:
            self._open_theme_picker()
            return
        if arg in ("list", "ls"):
            self._say_focused(
                f"mode {settings.mode} ({settings.mode_setting}) · "
                f"{settings.depth} · animations "
                f"{'on' if settings.animations else 'off'}"
            )
            for name in sorted(settings.themes):
                theme = settings.themes[name]
                mark = "▸" if name == self._palette.name else " "
                where = "" if theme.source == "builtin" else f"  [{theme.source}]"
                self._say_focused(f" {mark} {name} — {theme.description}{where}")
            return
        if arg not in settings.themes:
            self._say_focused(
                f"unknown theme {arg!r} — try /theme list", style="red",
            )
            return
        self._theme_settings = replace(settings, theme=arg)
        self.apply_palette(settings.palette(arg))
        self._say_focused(
            f'theme: {arg} ({settings.mode}) — persist it with [tui] theme = "{arg}"',
            style="green",
        )

    # -- budgets (#170) --------------------------------------------------
    def _budget_status_lines(self) -> list[str]:
        """The ``/budget`` inspector: per-lane + cohort caps and consumption."""
        lines: list[str] = []
        for lane in self._cohort.lanes:
            spec = lane.config.budget or getattr(lane.driver, "budget", None)
            tally = getattr(lane.driver, "budget_tally", None)
            desc = describe_budget(
                spec,
                cost_used=getattr(tally, "cost_usd", None),
                steps_used=getattr(tally, "llm_calls", None),
                wall_used=getattr(tally, "elapsed_sec", None),
                tool_used=getattr(tally, "tool_calls", None),
            )
            lines.append(f"{lane.label}: {desc}")
        cohort_spec = getattr(self._cohort, "budget", None)
        lines.append("cohort: " + describe_budget(
            cohort_spec,
            cost_used=self._cohort.total_cost,
            steps_used=self._cohort.total_steps,
            wall_used=self._elapsed(),
        ))
        target = "this lane" if self._single else "the cohort"
        lines.append(
            f"set: /budget <$0.10/20steps/300s> (sets {target}); "
            "/budget off clears it"
        )
        return lines

    def _handle_budget_command(self, text: str) -> None:
        """``/budget`` — inspect (no args) or set/clear the budget (#170).

        With no argument it lists every lane's budget and the cohort cap with
        live consumption. With a compact budget string it sets the focused
        lane's budget in single-lane mode, or the cohort-aggregate budget in
        multi-lane mode; ``off`` / ``none`` clears it.
        """
        lane = self._focused_lane()
        pane = self._pane(lane.id) if lane is not None else None

        def say(msg: str, style: str = "dim") -> None:
            if pane is not None:
                pane.note(msg, style=style)

        arg = text[len("/budget"):].strip()
        if not arg:
            for line in self._budget_status_lines():
                say(line)
            return
        clearing = arg.lower() in ("off", "none", "clear")
        spec = None
        if not clearing:
            try:
                spec = parse_budget_spec(arg)
            except ValueError as exc:
                say(f"bad budget: {exc}", style="red")
                return
            if spec is None:
                say("no positive cap in that budget — nothing set", style="red")
                return
        if self._single and lane is not None:
            # Record the cap on the lane config (drives the meter + manifest) and
            # arm the enforcer when the driver supports it (a real AgentDriver
            # does; an external lane cannot enforce, so it only records).
            setter = getattr(lane.driver, "set_budget", None)
            if setter is not None:
                setter(spec)
            lane.config.budget = spec
            say("lane budget cleared" if spec is None else f"lane budget set: {arg}",
                style="green")
        else:
            self._cohort.budget = spec
            self._cohort_cancelled.clear()  # a fresh cap re-arms enforcement
            say("cohort budget cleared" if spec is None else f"cohort budget set: {arg}",
                style="green")
        self._refresh_global()

    # -- in-TUI cohort resume (§13.2, interactive) -----------------------
    def _say_focused(self, msg: str, style: str = "dim") -> None:
        lane = self._focused_lane()
        if lane is not None:
            self._pane(lane.id).note(msg, style=style)

    def request_resume(self, cohort_id: str) -> None:
        """Switch to a saved cohort: exit this app with a resume request.

        The outer run loop persists + tears down the current cohort first, so
        nothing is lost, then relaunches on the requested one. Refused while a
        lane is mid-turn — cancel or let it finish first.
        """
        if any(lane.telemetry.busy for lane in self._cohort.lanes):
            self._say_focused(
                "lanes are still running — cancel (Ctrl+C) or wait, then /resume",
                style="red",
            )
            return
        known = {row["cohort_id"] for row in Cohort.list_saved(root=self._persist_root)}
        if cohort_id not in known:
            self._say_focused(f"no saved cohort {cohort_id!r} — try /cohorts", style="red")
            return
        if cohort_id == self._cohort.cohort_id:
            self._say_focused("that is the current cohort", style="red")
            return
        self.resume_request = cohort_id
        self.exit()

    def _open_cohort_picker(self) -> None:
        rows = Cohort.list_saved(root=self._persist_root)
        if not rows:
            self._say_focused("(no saved cohorts yet — they persist on exit)")
            return
        self.push_screen(CohortPickerScreen(rows), self._cohort_picked)

    def _cohort_picked(self, cohort_id: Any) -> None:
        """Dismissal callback for the cohort picker (``None`` = cancelled)."""
        if cohort_id:
            self.request_resume(str(cohort_id))

    # -- permission approvals (#171, opt-in — see run_multiplexer) -------
    def _pump_approvals(self) -> None:
        """Show queued permission requests as modals, one at a time (FIFO).

        Interval callback, active only when an :class:`ApprovalBroker` was
        wired. While a modal is up, later requests wait in the broker queue;
        a request withdrawn mid-modal (lane cancelled / timed out) retires
        its stale modal so the queue keeps moving. Substance lives in
        :mod:`chimera.tui.approvals`; this is only the presentation pump.
        """
        broker = self._approval_broker
        if broker is None:
            return
        active = self._active_approval
        if active is not None:
            if active.withdrawn and isinstance(self.screen, ApprovalModal):
                self.screen.dismiss(None)
            return
        pending = broker.next_pending()
        if pending is None:
            return
        self._active_approval = pending
        pane = self._pane_by_id.get(pending.lane_id)
        if pane is not None:
            pane.note(
                f"⏸ approval needed: {pending.request.tool_name}", style="yellow",
            )

        def _decided(outcome: Any) -> None:
            self._active_approval = None
            broker.resolve_with_outcome(pending, outcome)
            if pane is not None:
                allowed = bool(getattr(outcome, "approved", False))
                pane.note(
                    "· approved" if allowed else "· denied",
                    style="green" if allowed else "red",
                )

        self.push_screen(ApprovalModal(pending), _decided)


class CohortPickerScreen(FuzzySelectScreen):
    """In-TUI list of saved cohorts: Enter resumes the highlighted one.

    Reached via ``/cohorts`` (or bare ``/resume``); an instance of the
    universal fuzzy-select (R-OVER-2) — type to filter by cohort id, lane
    label, or task. The screen dismisses with the chosen cohort id (``None``
    on Esc); :meth:`MultiplexApp._cohort_picked` asks the app to exit with a
    resume request, and the outer run loop persists the current cohort before
    relaunching on the chosen one. Newest cohort is pre-highlighted.

    Bindings come from :class:`FuzzySelectScreen` (Esc cancels; printable keys
    filter). Folding the keybinding registry's ``pager`` context onto the
    select component (so user rebinds reach it) is tracked follow-up work
    alongside ``ResultsScreen``.
    """

    def __init__(self, rows: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(
            [self._item(row) for row in rows],
            title=f"Saved cohorts · {len(rows)} · Enter resumes · Esc back",
            placeholder="Type to filter saved cohorts…",
            **kwargs,
        )

    @staticmethod
    def _item(row: dict[str, Any]) -> SelectItem:
        labels = ", ".join(str(ln.get("label")) for ln in row.get("lanes", []))
        task = row.get("task") or "—"
        if len(task) > 60:
            task = task[:59] + "…"
        cohort_id = str(row["cohort_id"])
        return SelectItem(
            value=cohort_id,
            label=cohort_id,
            description=f"[{labels}]  ·  {task}",
            hint=str(row.get("created_at", "?")),
            search_text=f"{labels} {task}",
            id=cohort_id,
        )


class ThemePickerScreen(FuzzySelectScreen):
    """In-TUI theme picker with live preview and restore-on-cancel (R-THEME-3).

    Another instance of the universal fuzzy-select (R-OVER-2): type to filter
    by name, description, or source file. Moving the highlight *previews* the
    theme immediately (the app repaints behind the modal); Enter keeps it and
    Esc restores whatever was active when the picker opened — the caller wires
    both through the ``on_preview`` callback and the dismissal value.

    Args:
        themes: The catalog to offer (built-ins plus user theme files).
        current: Name of the active theme (pre-highlighted).
        mode: Resolved dark/light mode, shown in the title.
        depth: Resolved color depth, shown in the title.
        on_preview: Called with a theme name whenever the highlight moves.
    """

    def __init__(
        self,
        themes: list[Any],
        *,
        current: str = "",
        mode: str = "dark",
        depth: str = "truecolor",
        on_preview: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            [self._item(theme) for theme in themes],
            title=f"Themes · {mode} · {depth} · Enter keeps · Esc restores",
            placeholder="Type to filter themes…",
            initial=current or None,
            **kwargs,
        )
        self._on_preview = on_preview

    @staticmethod
    def _item(theme: Any) -> SelectItem:
        builtin = theme.source == "builtin"
        return SelectItem(
            value=theme.name,
            label=theme.name,
            description=theme.description or "(no description)",
            hint="built-in" if builtin else "user",
            search_text="" if builtin else str(theme.source),
            id=f"theme-{theme.name}",
        )

    @on(OptionList.OptionHighlighted, "#select-list")
    def _preview_highlighted(self, event: Any) -> None:
        """Live preview: repaint the app behind the modal as the cursor moves."""
        if self._on_preview is None:
            return
        option = getattr(event, "option", None)
        option_id = str(getattr(option, "id", "") or "")
        if option_id.startswith("theme-"):
            self._on_preview(option_id[len("theme-"):])


def parse_lane_specs(models: list[str] | str, default_preset: str = "coding_agent") -> list[dict[str, str]]:
    """Parse ``--models`` into lane specs.

    Each entry is ``model``, ``model:preset``, ``model:preset:loop``, or
    ``model:preset:loop:budget`` — the three per-lane comparison axes (§13.3)
    plus an optional per-lane budget override (#170) — or ``ext:<profile>``, an
    **external-agent lane** (issue #169): a real third-party coding-agent CLI
    named by an :func:`~chimera.assembly.external_driver.resolve_external_profile`
    profile, raced beside Chimera lanes. External entries record
    ``preset="external"`` and carry the profile name under ``external``; they
    take no preset/loop/budget axes (those belong to the external tool itself).

    The 4th ``budget`` field is a compact budget string
    (:func:`~chimera.tui.budget.parse_budget_spec`), e.g.
    ``glm-5.2:coding_agent:plan:$0.10/20steps``; it overrides the uniform
    ``--lane-budget`` / ``[tui.budget]`` default for just that lane. Reaching it
    needs the preset/loop fields present (empty is fine):
    ``glm-5.2:::$0.05`` budgets one lane at defaults.

    Lane ids are ``A``, ``B``, …; labels are the model with ``·preset`` /
    ``·loop`` appended when they differ from the default, and ``#k`` to
    disambiguate duplicates (the budget is a resource cap, not a comparison
    axis, so it never enters the label).

    Raises:
        ValueError: on an unknown preset, loop posture, external profile, too
            many ``:`` fields, or an unparseable budget clause.
    """
    from chimera.assembly.coding_agent import LOOP_POSTURES
    from chimera.assembly.external_driver import (
        EXTERNAL_LANE_PREFIX,
        EXTERNAL_LANE_PRESET,
        resolve_external_profile,
    )
    from chimera.assembly.loop_adapter import REAL_LOOPS
    from chimera.assembly.presets import DEPRECATED_PRESET_ALIASES, PRESETS

    valid_presets = set(PRESETS) | set(DEPRECATED_PRESET_ALIASES)
    valid_loops = set(LOOP_POSTURES) | set(REAL_LOOPS)
    items = models if isinstance(models, list) else models.split(",")
    raw = [m.strip() for m in items if m.strip()]
    parsed: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        parts = [p.strip() for p in item.split(":")]
        lane_id = chr(65 + i) if i < 26 else f"L{i + 1}"
        if parts[0] == EXTERNAL_LANE_PREFIX:
            if len(parts) != 2 or not parts[1]:
                raise ValueError(
                    f"bad external lane spec {item!r}: use ext:<profile-name> "
                    f"(external lanes take no preset/loop axes)"
                )
            profile_name = parts[1]
            resolve_external_profile(profile_name)  # loud on unknown/invalid
            parsed.append({
                "model": f"{EXTERNAL_LANE_PREFIX}:{profile_name}",
                "preset": EXTERNAL_LANE_PRESET,
                "loop": "",
                "external": profile_name,
                "budget": "",
                "lane_id": lane_id,
                "base": f"{EXTERNAL_LANE_PREFIX}:{profile_name}",
            })
            continue
        if len(parts) > 4:
            raise ValueError(
                f"too many ':' fields in {item!r}; use model[:preset[:loop[:budget]]]"
            )
        model = parts[0]
        preset = parts[1] if len(parts) > 1 and parts[1] else default_preset
        loop = parts[2] if len(parts) > 2 and parts[2] else ""
        budget = parts[3] if len(parts) > 3 and parts[3] else ""
        if preset not in valid_presets:
            raise ValueError(
                f"unknown preset {preset!r} in {item!r}; choose from {sorted(valid_presets)}"
            )
        if loop and loop not in valid_loops:
            raise ValueError(
                f"unknown loop {loop!r} in {item!r}; choose from {sorted(valid_loops)}"
            )
        if budget:
            parse_budget_spec(budget)  # loud on an unparseable clause
        base = model
        if preset != default_preset:
            base += f"·{preset}"
        if loop:
            base += f"·{loop}"
        parsed.append({
            "model": model, "preset": preset, "loop": loop, "external": "",
            "budget": budget, "lane_id": lane_id, "base": base,
        })

    totals = Counter(p["base"] for p in parsed)
    seen: Counter[str] = Counter()
    for p in parsed:
        if totals[p["base"]] > 1:
            seen[p["base"]] += 1
            p["label"] = f"{p['base']}#{seen[p['base']]}"
        else:
            p["label"] = p["base"]
    return parsed


def run_multiplexer(
    models: list[str] | str,
    project_dir: str | None = None,
    preset: str = "coding_agent",
    *,
    task: str | None = None,
    isolation: str = "auto",
    lane_cap: int | None = None,
    export: str | None = None,
    persist_root: str | None = None,
    approvals: bool | None = None,
    lane_budget: Any = None,
    cohort_budget: Any = None,
    mouse: bool = True,
    **agent_kwargs: Any,
) -> str | None:
    """Provision isolated workspaces, build the cohort, run the multiplexer.

    On exit the cohort is persisted (manifest + transcripts + diffs) *before*
    the ephemeral workspaces are torn down, and optionally exported to a zip.
    Returns the persisted cohort directory (or ``None`` if nothing ran).

    Args:
        approvals: Opt-in for permission-approval modals (#171). ``True``
            wires an :class:`~chimera.tui.approvals.ApprovalBroker` into every
            lane driver as its ``permission_callback``, so gated tool calls
            pause on a modal instead of auto-approving. ``None`` (default)
            defers to the ``CHIMERA_TUI_APPROVALS`` env var; ``False``/unset
            keeps today's behavior (the assembled agent's BYPASS posture).
        lane_budget: Per-lane budget applied to every lane (#170) — a
            :class:`~chimera.core.budget.BudgetSpec` or a compact string
            (``"$0.10/20steps/300s"``). A per-lane ``:budget`` field in the
            model spec overrides it for that lane; ``None`` falls back to the
            ``[tui.budget]`` config default.
        cohort_budget: Cohort-aggregate budget (#170): a cap on total $ / total
            steps / race wall-clock that cancels still-running lanes when
            tripped. ``None`` falls back to the ``[tui.budget.cohort]`` config.
    """
    if not sys.stdout.isatty():
        raise SystemExit(
            "the multiplexer needs an interactive terminal (a TTY); "
            "run it directly, not piped."
        )

    from chimera.assembly.driver import AgentDriver, DriverProtocol
    from chimera.tui.workspace import provision_workspaces

    try:
        specs = parse_lane_specs(models, default_preset=preset)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not specs:
        raise SystemExit("no models given (use --models glm-5.2,glm-5.1)")

    source = os.path.abspath(project_dir or os.getcwd())
    workspaces = provision_workspaces(source, [s["lane_id"] for s in specs], strategy=isolation)

    # -- budgets (#170): args win, else the [tui.budget] config defaults ----
    lane_default_budget, cohort_spec = _resolve_budgets(source, lane_budget, cohort_budget)

    # -- permission approvals (#171): explicit opt-in; default unchanged ----
    broker = ApprovalBroker() if approvals_enabled(approvals) else None

    # From here until app.run()'s own try/finally takes over, any failure
    # (driver construction — bad model / preset / loop spec, provider errors —
    # or Ctrl+C) must roll the worktrees back, or they leak with no cohort
    # artifact to explain them.
    try:
        lanes: list[Lane] = []
        for spec, ws in zip(specs, workspaces):
            lane_loop = spec.get("loop") or None
            # Per-lane budget (#170): the spec's ``:budget`` override, else the
            # uniform default. External lanes carry none (they cannot enforce it).
            lane_spec_budget = None
            driver: DriverProtocol
            if spec.get("external"):
                # External-agent lane (#169): a real third-party CLI runs in
                # this lane's worktree. Approval brokering and agent kwargs
                # are Chimera-loop machinery — they do not apply here.
                from chimera.assembly.external_driver import (
                    ExternalAgentDriver,
                    resolve_external_profile,
                )

                driver = ExternalAgentDriver(
                    resolve_external_profile(spec["external"]),
                    workdir=str(ws.path),
                )
            else:
                lane_spec_budget = parse_budget_spec(spec.get("budget")) or lane_default_budget
                lane_kwargs = dict(agent_kwargs)
                if broker is not None:
                    lane_kwargs["permission_callback"] = broker.handler_for(
                        spec["lane_id"], spec["label"],
                    )
                if lane_spec_budget is not None:
                    lane_kwargs["budget"] = lane_spec_budget
                driver = AgentDriver(
                    model=spec["model"],
                    project_dir=str(ws.path),
                    preset=spec["preset"],
                    loop=lane_loop,
                    **lane_kwargs,
                )
            config = LaneConfig(
                lane_id=spec["lane_id"],
                label=spec["label"],
                model=spec["model"],
                preset=spec["preset"],
                loop=lane_loop,
                budget=lane_spec_budget,
            )
            lanes.append(Lane(config, driver, ws))

        cohort = Cohort(
            lanes,
            task=task,
            source=source,
            isolation=workspaces.strategy,
            # Routing modes only mean something with 2+ lanes; a lone lane is
            # the daily driver and always addresses itself.
            routing=(
                RoutingMode.TARGETED if len(lanes) == 1 else RoutingMode.BROADCAST
            ),
            workspaces=workspaces,
            budget=cohort_spec,
        )
    except BaseException:
        workspaces.cleanup_all()
        raise

    return _run_cohort_loop(
        cohort, workspaces,
        lane_cap=lane_cap, initial_task=task, persist_root=persist_root,
        export=export, approval_broker=broker, mouse=mouse, **agent_kwargs,
    )


def _resolve_inline(project_dir: str | None, inline_arg: bool) -> bool:
    """Resolve whether inline (native-scrollback) mode was requested.

    An explicit argument (the ``--inline`` CLI flag) wins; otherwise the
    ``[tui] inline`` config knob supplies the default (§11; ``false``). Config
    discovery is best-effort — a broken config never blocks a launch.
    """
    if inline_arg:
        return True
    try:
        from chimera.config.user_config import load_tui_config

        tui = load_tui_config(project_dir)
    except Exception:  # noqa: BLE001 — config discovery must not block a launch
        return False
    return bool(tui.get("inline", False))


def _run_inline_single(
    cohort: Cohort,
    workspaces: Any,
    *,
    lane: Lane,
    initial_task: str | None,
    persist_root: str | None,
    export: str | None,
) -> str | None:
    """Drive one lane through the inline frontend, then finalize the cohort.

    The native-scrollback counterpart to :func:`_run_cohort_loop`: no
    full-screen app and no in-TUI cohort resume (a full-screen affordance), but
    the *same* persistence — :func:`_finalize_cohort` runs in ``finally`` so a
    crash still captures the artifact and tears down the workspaces.
    """
    from chimera.tui.inline_frontend import run_inline

    # Same theme chain as the full-screen app (R-THEME-1..4); best-effort, so a
    # broken theme file never blocks an inline launch.
    settings = load_theme_settings(cohort.source or None)
    cohort_dir = None
    try:
        run_inline(
            lane,
            initial_task=initial_task,
            palette=settings.palette(),
            animations=settings.animations,
        )
    finally:
        cohort_dir = _finalize_cohort(
            cohort, workspaces, persist_root=persist_root, export=export,
        )
    print(f"cohort saved: {cohort_dir}")
    if export:
        print(f"exported: {export}")
    return str(cohort_dir) if cohort_dir else None


def run_single_agent(
    model: str = "glm-5.2",
    project_dir: str | None = None,
    preset: str = "coding_agent",
    *,
    task: str | None = None,
    export: str | None = None,
    persist_root: str | None = None,
    lane_budget: Any = None,
    inline: bool = False,
    mouse: bool = True,
    **agent_kwargs: Any,
) -> str | None:
    """Run the daily-driver single-agent TUI: the multiplexer with N=1.

    Bare ``chimera code --tui`` lands here (issue #172): one ``inplace`` lane —
    the agent edits the real tree, daily-driver style — with targeted routing
    and the single-lane chrome (no tabstrip, no pane border/header, an
    app-style status line). Unlike a ``--models`` spec, *model* reaches the
    driver **verbatim** (never split on ``:``), so provider-tagged model names
    survive.

    On exit the session persists as a one-lane cohort under
    ``~/.chimera/cohorts/`` — resumable via ``--resume`` / ``/cohorts`` — the
    same lifecycle as any multiplexer run.

    Args:
        model: Model name, passed to :class:`AgentDriver` unmodified.
        project_dir: The tree the agent works in (default: cwd).
        preset: Assembly preset for the lane.
        task: Optional first task, auto-submitted on launch.
        export: Optional zip path for the persisted cohort artifact.
        persist_root: Override the cohort persistence root (used by tests).
        lane_budget: Budget for the lane (#170) — a
            :class:`~chimera.core.budget.BudgetSpec` or a compact string
            (``"$0.10/20steps"``); ``None`` falls back to ``[tui.budget]``.
        inline: Opt into the native-scrollback inline frontend (R-VIEW-5): the
            transcript flows into the terminal's own scrollback with the
            composer/status band pinned at the bottom (native selection, copy,
            wheel-scroll, after-exit persistence). Default ``False`` (unchanged
            full-screen). ``None``/``False`` falls back to the ``[tui] inline``
            config knob. Honored only where
            :func:`~chimera.tui.scrollback.inline_capability` clears it (POSIX,
            interactive TTY, no scrollback-hostile multiplexer); otherwise the
            full-screen frontend runs and a one-line note explains why —
            scrollback is never silently lost.
        **agent_kwargs: Extra :class:`AgentDriver` kwargs (e.g. ``max_turns``).

    Returns:
        The persisted cohort directory (or ``None`` if nothing ran).
    """
    if not sys.stdout.isatty():
        raise SystemExit(
            "the TUI needs an interactive terminal (a TTY); run it directly, not piped."
        )

    from chimera.assembly.driver import AgentDriver
    from chimera.tui.workspace import provision_workspaces

    source = os.path.abspath(project_dir or os.getcwd())
    workspaces = provision_workspaces(source, ["A"], strategy="inplace")
    # Budget (#170): the lane's own cap; for one lane it is also the cohort cap.
    lane_spec_budget, _ = _resolve_budgets(source, lane_budget, None)
    try:
        ws = workspaces[0]
        lane_kwargs = dict(agent_kwargs)
        if lane_spec_budget is not None:
            lane_kwargs["budget"] = lane_spec_budget
        driver = AgentDriver(
            model=model, project_dir=str(ws.path), preset=preset, **lane_kwargs,
        )
        config = LaneConfig(
            lane_id="A", label=model, model=model, preset=preset,
            budget=lane_spec_budget,
        )
        cohort = Cohort(
            [Lane(config, driver, ws)],
            task=task,
            source=source,
            isolation=workspaces.strategy,
            routing=RoutingMode.TARGETED,
            workspaces=workspaces,
        )
    except BaseException:
        workspaces.cleanup_all()
        raise

    # Inline (native-scrollback) mode is opt-in and gated (R-VIEW-5): the flag
    # or config knob asks, and inline_capability enforces POSIX / interactive
    # TTY / no scrollback-hostile multiplexer. On refusal, fall back to the
    # full-screen frontend with a note — never a silent loss of scrollback.
    decision = inline_capability(_resolve_inline(source, inline))
    if decision.use_inline:
        return _run_inline_single(
            cohort, workspaces, lane=cohort.lanes[0],
            initial_task=task, persist_root=persist_root, export=export,
        )
    if decision.refused:
        sys.stderr.write(
            f"inline mode unavailable ({decision.reason}); using the full-screen TUI.\n"
        )

    return _run_cohort_loop(
        cohort, workspaces,
        initial_task=task, persist_root=persist_root, export=export,
        mouse=mouse, **agent_kwargs,
    )


def _finalize_cohort(
    cohort: Cohort,
    workspaces: Any,
    *,
    persist_root: str | None,
    export: str | None,
) -> Any:
    """Persist the cohort artifact, optionally export, prune, tear down workspaces.

    The shared end-of-session ritual for both frontends (full-screen and
    inline): capture the artifact *before* the ephemeral workspaces go away
    (diffs read from the live worktrees), best-effort export + retention prune
    (#173), then clean up. Extracted so the inline path (:func:`run_single_agent`
    with ``inline=True``) persists byte-identically to the full-screen loop.

    Returns:
        The persisted cohort directory.
    """
    # Capture the artifact BEFORE tearing down the workspaces (diffs read
    # from the live worktrees).
    cohort_dir = cohort.persist(root=persist_root)
    if export:
        try:
            cohort.export(export, cohort_dir=cohort_dir)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"export failed: {exc}\n")
    # Auto-prune old cohorts per the retention policy (#173), never the one we
    # just saved. OFF by default; best-effort, never fatal.
    try:
        retention = load_cohort_retention()
        if retention.active:
            removed = prune_cohorts(
                root=persist_root,
                retention=retention,
                exclude=(cohort.cohort_id,),
            )
            if removed:
                print(f"pruned {len(removed)} old cohort(s)")
    except Exception:  # noqa: BLE001 - pruning must never break a session
        pass
    workspaces.cleanup_all()
    return cohort_dir


def _run_cohort_loop(
    cohort: Cohort,
    workspaces: Any,
    *,
    lane_cap: int | None = None,
    initial_task: str | None = None,
    persist_root: str | None = None,
    export: str | None = None,
    approval_broker: Any | None = None,
    mouse: bool = True,
    **agent_kwargs: Any,
) -> str | None:
    """Run cohorts until the user leaves without requesting an in-TUI resume.

    Each iteration owns one cohort's full lifecycle: run the app, persist the
    artifact *before* tearing down the workspaces, then either stop or relaunch
    on the saved cohort the user picked inside the TUI (``/resume``,
    ``/cohorts``). One cohort's teardown always completes before the next
    cohort starts, so switching never loses work.
    """
    cohort_dir = None
    task = initial_task
    while True:
        try:
            app = MultiplexApp(
                cohort, lane_cap=lane_cap, initial_task=task, persist_root=persist_root,
                approval_broker=approval_broker,
            )
            # mouse=False leaves the terminal's own mouse handling intact, so
            # native click-drag selection / copy / scrollback work (Track 1A).
            app.run(mouse=mouse)
        finally:
            cohort_dir = _finalize_cohort(
                cohort, workspaces, persist_root=persist_root, export=export,
            )
        print(f"cohort saved: {cohort_dir}")
        if export:
            print(f"exported: {export}")
        requested = app.resume_request
        if not requested:
            break
        task = None
        try:
            cohort, workspaces = _load_saved_cohort(
                requested, isolation=None, persist_root=persist_root,
                approval_broker=approval_broker, **agent_kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - never crash after a clean session
            print(f"resume failed: {exc}")
            break
    return str(cohort_dir) if cohort_dir else None


def _load_saved_cohort(
    cohort_id: str,
    *,
    isolation: str | None = None,
    persist_root: str | None = None,
    approval_broker: Any | None = None,
    **agent_kwargs: Any,
) -> tuple[Cohort, Any]:
    """Rebuild a saved cohort for resume (spec §13.2).

    Fresh workspaces from the recorded base commit with each lane's saved diff
    re-applied, drivers seeded with saved history, telemetry restored. The
    isolation strategy resolves by lane count unless *isolation* is explicit.

    Raises:
        FileNotFoundError: Unknown cohort id.
    """
    from chimera.assembly.driver import AgentDriver, DriverProtocol
    from chimera.tui.history_io import deserialize_history
    from chimera.tui.workspace import apply_diff, provision_workspaces

    saved = Cohort.load_saved(cohort_id, root=persist_root)
    manifest = saved["manifest"]
    lane_specs = saved["lanes"]
    source = manifest.get("source") or os.getcwd()
    task = manifest.get("task")
    base_commit = next(
        ((ls.get("workspace") or {}).get("base_commit") for ls in lane_specs
         if (ls.get("workspace") or {}).get("base_commit")),
        None,
    )

    lane_ids = [ls["lane_id"] for ls in lane_specs]
    workspaces = provision_workspaces(
        source, lane_ids,
        strategy=default_isolation(len(lane_ids), isolation),
        base_commit=base_commit,
    )

    lanes: list[Lane] = []
    for spec, ws in zip(lane_specs, workspaces):
        if spec.get("diff"):
            apply_diff(ws.path, spec["diff"])  # restore produced changes (best-effort)
        preset = spec.get("preset") or "coding_agent"
        lane_loop = spec.get("loop") or None
        # Restore the lane's saved budget (#170); external lanes carry none.
        lane_budget_spec = budget_from_dict(spec.get("budget"))
        model = str(spec["model"])
        driver: DriverProtocol
        if model.startswith("ext:"):
            # External-agent lane (#169): rebuild from its profile; the saved
            # minimal history seeds the transcript context for the artifact.
            from chimera.assembly.external_driver import (
                ExternalAgentDriver,
                resolve_external_profile,
            )

            driver = ExternalAgentDriver(
                resolve_external_profile(model.split(":", 1)[1]),
                workdir=str(ws.path),
            )
            lane_budget_spec = None
        else:
            lane_kwargs = dict(agent_kwargs)
            # -- permission approvals (#171): re-bind lane handlers on resume ---
            if approval_broker is not None:
                lane_kwargs["permission_callback"] = approval_broker.handler_for(
                    spec["lane_id"], spec.get("label", spec["lane_id"]),
                )
            if lane_budget_spec is not None:
                lane_kwargs["budget"] = lane_budget_spec
            driver = AgentDriver(
                model=model, project_dir=str(ws.path), preset=preset,
                loop=lane_loop, **lane_kwargs,
            )
        driver.load_history(deserialize_history(spec.get("history") or []))
        config = LaneConfig(
            lane_id=spec["lane_id"],
            label=spec.get("label", spec["lane_id"]),
            model=spec["model"],
            preset=preset,
            loop=lane_loop,
            budget=lane_budget_spec,
        )
        lane = Lane(config, driver, ws)
        tel = spec.get("telemetry") or {}
        lane.telemetry.cost = float(tel.get("cost", 0.0) or 0.0)
        lane.telemetry.steps = int(tel.get("steps", 0) or 0)
        lane.telemetry.turns = int(tel.get("turns", 0) or 0)
        lane.telemetry.tokens_in = int(tel.get("tokens_in", 0) or 0)
        lane.telemetry.tokens_out = int(tel.get("tokens_out", 0) or 0)
        lanes.append(lane)

    try:
        routing = RoutingMode(manifest.get("routing", "broadcast"))
    except ValueError:
        routing = RoutingMode.BROADCAST

    cohort = Cohort(
        lanes, task=task, source=source, isolation=workspaces.strategy,
        routing=routing, cohort_id=cohort_id, workspaces=workspaces,
        budget=budget_from_dict(manifest.get("budget")),
    )
    return cohort, workspaces


def print_saved_cohorts(persist_root: str | None = None) -> None:
    """Print persisted cohorts (id · when · task · lanes) for --resume discovery."""
    rows = Cohort.list_saved(root=persist_root)
    if not rows:
        print("no saved cohorts yet.")
        return
    print(f"{len(rows)} saved cohort(s):")
    for row in rows:
        labels = ", ".join(f"{ln['label']}" for ln in row["lanes"])
        task = (row["task"] or "—")
        if len(task) > 50:
            task = task[:49] + "…"
        print(f"  {row['cohort_id']}  ·  {row.get('created_at', '?')}  ·  [{labels}]  ·  {task!r}")


def resume_multiplexer(
    cohort_id: str,
    *,
    isolation: str | None = None,
    lane_cap: int | None = None,
    export: str | None = None,
    persist_root: str | None = None,
    approvals: bool | None = None,
    mouse: bool = True,
    **agent_kwargs: Any,
) -> str | None:
    """Reopen a saved cohort and continue it (spec §13.2).

    Reconstructs each lane: a fresh workspace from the recorded base commit with
    the lane's saved diff re-applied, a driver seeded with the lane's saved
    history, and its telemetry restored so the scoreboard keeps accumulating. The
    lanes start idle; the next broadcast continues the race. Isolation resolves
    by lane count (single lane → inplace) unless given explicitly. From inside
    the running app, ``/cohorts`` / ``/resume`` switch cohorts the same way.
    """
    if not sys.stdout.isatty():
        raise SystemExit(
            "the multiplexer needs an interactive terminal (a TTY); run it directly."
        )

    # -- permission approvals (#171): explicit opt-in; default unchanged ----
    broker = ApprovalBroker() if approvals_enabled(approvals) else None

    try:
        cohort, workspaces = _load_saved_cohort(
            cohort_id, isolation=isolation, persist_root=persist_root,
            approval_broker=broker, **agent_kwargs,
        )
    except FileNotFoundError as exc:
        print(str(exc))
        print_saved_cohorts(persist_root)
        raise SystemExit(1) from exc

    return _run_cohort_loop(
        cohort, workspaces,
        lane_cap=lane_cap, initial_task=None, persist_root=persist_root,
        export=export, approval_broker=broker, mouse=mouse, **agent_kwargs,
    )

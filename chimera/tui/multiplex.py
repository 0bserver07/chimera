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
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from typing import Any

try:
    from rich.text import Text
    from textual import on
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import Screen
    from textual.widgets import Footer, Header, OptionList, RichLog, Static, TextArea
    from textual.widgets.option_list import Option

    from chimera.tui.prompt import PromptArea, filter_commands
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The Chimera multiplexer needs the 'tui' extra:\n"
        "  pip install 'chimera-run[tui]'   (or: pip install textual)"
    ) from exc

from chimera.core.loop_events import LoopEventType
from chimera.tui.cohort import Cohort
from chimera.tui.lane import Lane, LaneConfig
from chimera.tui.render import LaneTranscript
from chimera.tui.routing import Action, RoutingMode, route

__all__ = [
    "MultiplexApp",
    "LanePane",
    "run_multiplexer",
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

# A pane narrower than this is unreadable; below it we degrade to tabs (§6.3).
MIN_PANE_WIDTH = 32
_HEADER_REFRESH_EVENTS = frozenset({
    LoopEventType.tool_use, LoopEventType.tool_result, LoopEventType.result,
})


class LanePane(Vertical):
    """One lane rendered as a pane: a status header over a scrolling transcript.

    A pane is essentially a Phase-1 TUI reduced to a widget. It renders the
    lane's event stream via the shared :class:`LaneTranscript` so panes look
    identical to the single-agent TUI.
    """

    def __init__(self, lane: Lane, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lane = lane
        self._transcript: LaneTranscript | None = None
        self._is_focus = False

    def compose(self) -> ComposeResult:
        yield Static(self._header_text(), classes="lane-header")
        yield RichLog(classes="lane-log", wrap=True, markup=False, highlight=False)

    def on_mount(self) -> None:
        self._transcript = LaneTranscript(self.query_one(RichLog).write)

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
        self.query_one(".lane-header", Static).update(self._header_text())

    def feed(self, ev: Any) -> None:
        if self._transcript is not None:
            self._transcript.handle(ev)

    def commit(self) -> None:
        if self._transcript is not None:
            self._transcript.commit()

    def echo_user(self, text: str) -> None:
        self.query_one(RichLog).write(Text.assemble(("› ", "bold cyan"), (text, "bold")))

    def note(self, text: str, style: str = "magenta") -> None:
        self.query_one(RichLog).write(Text(text, style=style))

    def feed_error(self, exc: object) -> None:
        self.query_one(RichLog).write(Text(f"turn failed: {exc}", style="red"))

    def intro(self) -> None:
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

    def reveal_reasoning(self) -> bool:
        return self._transcript.reveal_last() if self._transcript is not None else False


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
    .lane-header { height: 1; background: $boost; padding: 0 1; }
    .lane-log { height: 1fr; padding: 0 1; }
    #sidebar { width: 32; border: round $secondary; padding: 0 1; }
    #prompt { border: round $secondary; height: auto; max-height: 8; }
    #hint { height: 1; color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel_all", "Cancel all / quit", priority=True),
        Binding("ctrl+d", "quit", "Quit"),
        # Tab completes a "/" command when one is being typed, else cycles focus.
        Binding("tab", "smart_tab", "Complete / focus →", priority=True),
        Binding("shift+tab", "focus_prev_lane", "Focus ←", priority=True),
        Binding("ctrl+b", "toggle_broadcast", "Broadcast/target"),
        Binding("ctrl+g", "cancel_focused", "Cancel lane"),
        Binding("ctrl+o", "clear_focused", "Clear lane"),
        Binding("ctrl+r", "show_results", "Compare results"),
        Binding("ctrl+e", "toggle_reasoning", "Reasoning"),
        Binding("ctrl+t", "toggle_sidebar", "Sidebar"),
    ]

    #: Local slash-command catalog (drives /help and slash autocomplete).
    #: NOT named ``COMMANDS`` — that shadows Textual's command-palette provider
    #: registry on ``App``, and the palette then crashes on Ctrl+P trying to
    #: instantiate strings as providers.
    SLASH_COMMANDS = [
        "/broadcast", "/clear", "/cohorts", "/cost", "/exit", "/export",
        "/help", "/model", "/quit", "/resume", "/results", "/summary",
        "/target", "/tools",
    ]

    def __init__(
        self,
        cohort: Cohort,
        *,
        lane_cap: int | None = None,
        initial_task: str | None = None,
        persist_root: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._cohort = cohort
        self._mode = cohort.routing
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
        self._slots: Any = None  # asyncio.Semaphore, created on mount
        self._show_reasoning = False
        self._sidebar_on = False
        #: Set by /resume (or the /cohorts picker) just before exit; the outer
        #: run loop reads it and relaunches on the requested saved cohort.
        self.resume_request: str | None = None

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(self._global_status_text(), id="global-status")
        yield Static("", id="summary")
        yield Static("", id="tabstrip")
        with Horizontal(id="lanes"):
            for lane in self._cohort.lanes:
                pane = LanePane(lane, classes="lane-pane")
                self._panes.append(pane)
                self._pane_by_id[lane.id] = pane
                yield pane
            yield Static("", id="sidebar")
        yield Static("", id="hint")
        yield PromptArea(placeholder=self._placeholder(), commands=self.SLASH_COMMANDS, id="prompt")
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
            pane.intro()
        self.set_interval(0.5, self._refresh_global)
        if self._initial_task:
            self._submit_text(self._initial_task)

    def on_resize(self) -> None:
        self._relayout()

    def _relayout(self) -> None:
        n = len(self._panes)
        width = self.size.width or 80
        self._tabbed = (width // max(n, 1)) < MIN_PANE_WIDTH
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
        if self._mode is RoutingMode.BROADCAST:
            return "Broadcast to all lanes, or /help …"
        lane = self._focused_lane()
        return f"Target @{lane.label if lane else '?'}, or /help …"

    def _global_status_text(self) -> str:
        c = self._cohort
        n = len(c.lanes)
        mode = self._mode.value
        if self._race_start is None:
            return f" idle · lanes: {n} · Σ$0.0000 · [{mode}]  (type a task, Enter to race)"
        first = c.first_finisher
        task = c.task or "—"
        if len(task) > 40:
            task = task[:39] + "…"
        return (
            f" task: {task!r} · lanes: {n} · done: {c.done_count}/{n} · "
            f"Σ${c.total_cost:.4f} · {self._elapsed():.1f}s · "
            f"first: {first.label if first else '—'} · [{mode}]"
        )

    def _elapsed(self) -> float:
        if self._race_start is None:
            return 0.0
        end = self._race_end if self._race_end is not None else time.monotonic()
        return end - self._race_start

    def _refresh_global(self) -> None:
        # Interval-timer callback: it can race app teardown (the widget tree is
        # already unmounting), so a missing node is a no-op, not an error.
        nodes = self.query("#global-status")
        if nodes:
            nodes.first(Static).update(self._global_status_text())

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
        summary = self.query_one("#summary", Static)
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
        text = event.value.strip()
        event.prompt.remember(event.value)
        event.prompt.value = ""
        self._hide_hint()
        self._submit_text(text)

    @on(TextArea.Changed, "#prompt")
    def _on_prompt_changed(self, event: TextArea.Changed) -> None:
        # Autocomplete hint (§13.6): show matching commands while a "/" prefix
        # is being typed; Tab completes.
        matches = filter_commands(event.text_area.text, self.SLASH_COMMANDS)
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
                pane.note(f"↳ steer: {text}")
                lane.note(f"↳ steer: {text}")
            elif a.action is Action.FOLLOW_UP:
                lane.driver.queue_follow_up(text)
                pane.note(f"↳ queued: {text}", style="cyan")
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
                pane.refresh_header()
                self._refresh_global()
                if self._cohort.all_done:
                    self._finish_race()

    def _finish_race(self) -> None:
        if self._race_start is not None and self._race_end is None:
            self._race_end = time.monotonic()
        self._show_summary()

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
        self._pane(lane.id).query_one(RichLog).clear()

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

        self.push_screen(ResultsScreen(self._cohort))

    def action_smart_tab(self) -> None:
        """Tab: complete a "/" command being typed, else cycle lane focus."""
        prompt = self.query_one("#prompt", PromptArea)
        if prompt.has_focus and prompt.text.lstrip().startswith("/"):
            from chimera.tui.prompt import complete_command

            completed = complete_command(prompt.text, self.SLASH_COMMANDS)
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
            say(
                "/help /model /cost /tools /clear /summary /results /export "
                "/cohorts /resume [id] /broadcast /target /exit  ·  "
                "Tab complete-or-focus · Ctrl+B mode · "
                "Ctrl+R compare · Ctrl+E reasoning · Ctrl+T sidebar · "
                "Ctrl+J newline · Ctrl+C cancel-all · Ctrl+G cancel-lane"
            )
        elif cmd == "/model":
            say("  ".join(f"{ln.label}={ln.config.model}" for ln in self._cohort.lanes))
        elif cmd == "/cost":
            say(f"Σ ${self._cohort.total_cost:.4f}  ·  " + "  ".join(
                f"{ln.label}=${ln.telemetry.cost:.4f}" for ln in self._cohort.lanes
            ))
        elif cmd == "/tools":
            if lane is not None:
                say(", ".join(t.name for t in lane.driver.tools) or "(none)")
        elif cmd == "/clear":
            self.action_clear_focused()
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
        self.push_screen(CohortPickerScreen(rows))


class CohortPickerScreen(Screen):
    """In-TUI list of saved cohorts: Enter resumes the highlighted one.

    Reached via ``/cohorts`` (or bare ``/resume``). Selecting a cohort asks the
    app to exit with a resume request; the outer run loop persists the current
    cohort and relaunches on the chosen one.
    """

    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("q", "close", "Back"),
    ]

    CSS = """
    #picker-title { height: 1; background: $primary; color: $text; padding: 0 1; }
    #picker-list { height: 1fr; }
    """

    def __init__(self, rows: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rows = rows

    def compose(self) -> ComposeResult:
        yield Static(
            Text(
                f" Saved cohorts · {len(self._rows)} · Enter resumes · Esc back",
                style="bold",
            ),
            id="picker-title",
        )
        yield OptionList(id="picker-list")
        yield Footer()

    def on_mount(self) -> None:
        picker = self.query_one(OptionList)
        for row in self._rows:
            labels = ", ".join(str(ln.get("label")) for ln in row.get("lanes", []))
            task = row.get("task") or "—"
            if len(task) > 46:
                task = task[:45] + "…"
            picker.add_option(Option(
                f"{row['cohort_id']}  ·  {row.get('created_at', '?')}  ·  "
                f"[{labels}]  ·  {task}",
                id=row["cohort_id"],
            ))
        if self._rows:
            picker.highlighted = 0  # keyboard-first: Enter resumes the newest
        picker.focus()

    @on(OptionList.OptionSelected)
    def _picked(self, event: OptionList.OptionSelected) -> None:
        app = self.app
        self.app.pop_screen()
        if isinstance(app, MultiplexApp) and event.option.id:
            app.request_resume(str(event.option.id))

    def action_close(self) -> None:
        self.app.pop_screen()


def parse_lane_specs(models: list[str] | str, default_preset: str = "coding_agent") -> list[dict[str, str]]:
    """Parse ``--models`` into lane specs.

    Each entry is ``model``, ``model:preset``, or ``model:preset:loop`` — the
    three per-lane comparison axes (§13.3). Lane ids are ``A``, ``B``, …; labels
    are the model with ``·preset`` / ``·loop`` appended when they differ from the
    default, and ``#k`` to disambiguate duplicates.

    Raises:
        ValueError: on an unknown preset or loop posture.
    """
    from chimera.assembly.coding_agent import LOOP_POSTURES
    from chimera.assembly.loop_adapter import REAL_LOOPS
    from chimera.assembly.presets import DEPRECATED_PRESET_ALIASES, PRESETS

    valid_presets = set(PRESETS) | set(DEPRECATED_PRESET_ALIASES)
    valid_loops = set(LOOP_POSTURES) | set(REAL_LOOPS)
    items = models if isinstance(models, list) else models.split(",")
    raw = [m.strip() for m in items if m.strip()]
    parsed: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        parts = [p.strip() for p in item.split(":")]
        model = parts[0]
        preset = parts[1] if len(parts) > 1 and parts[1] else default_preset
        loop = parts[2] if len(parts) > 2 and parts[2] else ""
        if preset not in valid_presets:
            raise ValueError(
                f"unknown preset {preset!r} in {item!r}; choose from {sorted(valid_presets)}"
            )
        if loop and loop not in valid_loops:
            raise ValueError(
                f"unknown loop {loop!r} in {item!r}; choose from {sorted(valid_loops)}"
            )
        base = model
        if preset != default_preset:
            base += f"·{preset}"
        if loop:
            base += f"·{loop}"
        lane_id = chr(65 + i) if i < 26 else f"L{i + 1}"
        parsed.append({
            "model": model, "preset": preset, "loop": loop,
            "lane_id": lane_id, "base": base,
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
    **agent_kwargs: Any,
) -> str | None:
    """Provision isolated workspaces, build the cohort, run the multiplexer.

    On exit the cohort is persisted (manifest + transcripts + diffs) *before*
    the ephemeral workspaces are torn down, and optionally exported to a zip.
    Returns the persisted cohort directory (or ``None`` if nothing ran).
    """
    if not sys.stdout.isatty():
        raise SystemExit(
            "the multiplexer needs an interactive terminal (a TTY); "
            "run it directly, not piped."
        )

    from chimera.assembly.driver import AgentDriver
    from chimera.tui.workspace import provision_workspaces

    try:
        specs = parse_lane_specs(models, default_preset=preset)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not specs:
        raise SystemExit("no models given (use --models glm-5.2,glm-5.1)")

    source = os.path.abspath(project_dir or os.getcwd())
    workspaces = provision_workspaces(source, [s["lane_id"] for s in specs], strategy=isolation)

    # From here until app.run()'s own try/finally takes over, any failure
    # (driver construction — bad model / preset / loop spec, provider errors —
    # or Ctrl+C) must roll the worktrees back, or they leak with no cohort
    # artifact to explain them.
    try:
        lanes: list[Lane] = []
        for spec, ws in zip(specs, workspaces):
            lane_loop = spec.get("loop") or None
            driver = AgentDriver(
                model=spec["model"],
                project_dir=str(ws.path),
                preset=spec["preset"],
                loop=lane_loop,
                **agent_kwargs,
            )
            config = LaneConfig(
                lane_id=spec["lane_id"],
                label=spec["label"],
                model=spec["model"],
                preset=spec["preset"],
                loop=lane_loop,
            )
            lanes.append(Lane(config, driver, ws))

        cohort = Cohort(
            lanes,
            task=task,
            source=source,
            isolation=workspaces.strategy,
            workspaces=workspaces,
        )
    except BaseException:
        workspaces.cleanup_all()
        raise

    return _run_cohort_loop(
        cohort, workspaces,
        lane_cap=lane_cap, initial_task=task, persist_root=persist_root,
        export=export, **agent_kwargs,
    )


def _run_cohort_loop(
    cohort: Cohort,
    workspaces: Any,
    *,
    lane_cap: int | None = None,
    initial_task: str | None = None,
    persist_root: str | None = None,
    export: str | None = None,
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
            )
            app.run()
        finally:
            # Capture the artifact BEFORE tearing down the workspaces (diffs
            # read from the live worktrees).
            cohort_dir = cohort.persist(root=persist_root)
            if export:
                try:
                    cohort.export(export, cohort_dir=cohort_dir)
                except Exception as exc:  # noqa: BLE001
                    sys.stderr.write(f"export failed: {exc}\n")
            workspaces.cleanup_all()
        print(f"cohort saved: {cohort_dir}")
        if export:
            print(f"exported: {export}")
        requested = app.resume_request
        if not requested:
            break
        task = None
        try:
            cohort, workspaces = _load_saved_cohort(
                requested, isolation=None, persist_root=persist_root, **agent_kwargs,
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
    **agent_kwargs: Any,
) -> tuple[Cohort, Any]:
    """Rebuild a saved cohort for resume (spec §13.2).

    Fresh workspaces from the recorded base commit with each lane's saved diff
    re-applied, drivers seeded with saved history, telemetry restored. The
    isolation strategy resolves by lane count unless *isolation* is explicit.

    Raises:
        FileNotFoundError: Unknown cohort id.
    """
    from chimera.assembly.driver import AgentDriver
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
        driver = AgentDriver(
            model=spec["model"], project_dir=str(ws.path), preset=preset,
            loop=lane_loop, **agent_kwargs,
        )
        driver.load_history(deserialize_history(spec.get("history") or []))
        config = LaneConfig(
            lane_id=spec["lane_id"],
            label=spec.get("label", spec["lane_id"]),
            model=spec["model"],
            preset=preset,
            loop=lane_loop,
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

    try:
        cohort, workspaces = _load_saved_cohort(
            cohort_id, isolation=isolation, persist_root=persist_root, **agent_kwargs,
        )
    except FileNotFoundError as exc:
        print(str(exc))
        print_saved_cohorts(persist_root)
        raise SystemExit(1) from exc

    return _run_cohort_loop(
        cohort, workspaces,
        lane_cap=lane_cap, initial_task=None, persist_root=persist_root,
        export=export, **agent_kwargs,
    )

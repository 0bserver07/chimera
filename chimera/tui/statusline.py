"""Customizable status line + terminal title for the Chimera TUIs (R-STAT-1..5).

All status-bar content comes from a registry of named :class:`StatusItem`\\ s
(R-STAT-1). Each item renders against a :class:`StatusContext` snapshot and
returns rich ``Text`` — or ``None`` to hide itself when its fact is
unavailable (presence-based omission: no placeholders, no invented numbers).
The visible line is an *ordered composition* of item ids from the TUI config
(``tui.status_line``), degraded per segment to the terminal width so the line
never wraps (R-STAT-2): items swap to their short form lowest-priority first,
left-truncatable items shrink, then whole items drop.

External facts never block the render loop (R-STAT-3):
:class:`GitFactsWatcher` resolves branch/dirty on a small daemon thread that
polls the repository HEAD by *path* — stat'ing the file fresh each tick and
also watching its parent directory's mtime, because git updates HEAD with an
atomic rename that swaps the inode out from under any handle-based watch —
debounced by the poll cadence (~200 ms) with a TTL fallback for facts that
don't touch HEAD at all (dirtiness). Renderers only ever read a lock-guarded
snapshot.

The terminal title mirrors a second ordered item list (``tui.title``,
R-STAT-5), sanitized of control/bidi characters. Textual's title mechanism
(``App.title`` / ``App.sub_title`` -> Header) carries it in-app; the real
terminal title is set via OSC 0 with the xterm title stack pushed first and
popped on exit, since Textual itself never touches the emulator title.

Module policy: stdlib only, plus ``rich.text.Text`` (ships with the ``tui``
extra). Nothing here imports textual, so every composition function is pure
and unit-testable without a TTY. Third-party segments registered through
:class:`chimera.plugins.ui.UIExtensionRegistry` can be bridged in by wrapping
them with :func:`register_item`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.text import Text

from chimera import __version__

if TYPE_CHECKING:
    from chimera.tui.lane import Lane

__all__ = [
    "DEFAULT_STATUS_LINE",
    "DEFAULT_STATUS_LINE_MULTI",
    "DEFAULT_TITLE",
    "GitFacts",
    "GitFactsWatcher",
    "StatusContext",
    "StatusItem",
    "StatusLine",
    "TerminalTitle",
    "all_items",
    "build_cohort_context",
    "build_lane_context",
    "compose_status",
    "compose_title",
    "format_tokens",
    "get_item",
    "load_tui_config",
    "parse_item_order",
    "register_item",
    "sanitize_title",
]

# Ordered defaults (spec §11): the single-lane daily driver shows the model's
# vitals; the multi-lane racing view keeps its scoreboard. Both are overridden
# by ``tui.status_line`` — one knob, every lane count.
DEFAULT_STATUS_LINE = ("model", "context-used", "cost", "run-state")
DEFAULT_STATUS_LINE_MULTI = (
    "task", "progress", "cost", "elapsed", "run-state", "mode", "hint",
)
DEFAULT_TITLE = ("activity", "project")

_SEPARATOR = " · "
# Context-meter thresholds (R-STAT-4): ambient truth, threshold-colored.
_CONTEXT_WARN = 0.70
_CONTEXT_ERROR = 0.90


# ---------------------------------------------------------------------------
# Context snapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GitFacts:
    """A thread-safe snapshot of repository facts for the ``git`` item.

    Attributes:
        branch: Current branch name, or ``None`` when detached / unknown.
        sha: Short commit id when HEAD is detached, else ``None``.
        dirty: ``True``/``False`` once ``git status`` has run; ``None`` while
            still unknown (never guessed).
    """

    branch: str | None = None
    sha: str | None = None
    dirty: bool | None = None


@dataclass
class StatusContext:
    """Everything a status/title renderer may read, already resolved.

    Frontends fill only what they truly know; every field defaults to
    "unavailable" so items hide rather than invent (R-STAT-1). Use
    :func:`build_lane_context` / :func:`build_cohort_context` rather than
    filling this by hand.
    """

    model: str | None = None
    reasoning: str | None = None
    project_dir: str | None = None
    git: GitFacts | None = None
    run_state: str | None = None
    busy: bool = False
    context_used: int | None = None
    context_window: int | None = None
    auto_compaction: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float | None = None
    lanes_total: int = 1
    lanes_done: int = 0
    first_label: str | None = None
    task: str | None = None
    elapsed: float | None = None
    mode: str | None = None
    hint: str | None = None
    version: str = __version__


def build_lane_context(lane: Lane, *, git: GitFacts | None = None) -> StatusContext:
    """Build the single-lane context from a lane's driver + telemetry.

    Availability sources (what is *real*, per driver/loop plumbing):

    - ``model`` — ``lane.config.model`` (always).
    - ``reasoning`` — ``driver.thinking`` if a driver ever exposes it; the
      current :class:`~chimera.assembly.driver.AgentDriver` does not, so the
      item stays hidden (hide-when-unavailable, not a fake).
    - ``context_used`` — ``telemetry.context_tokens``: the prompt-side token
      count of the *latest* provider request, observed from per-step
      ``assistant`` events (provider-reported usage). Zero until a provider
      reports usage; the meter hides rather than estimate.
    - ``context_window`` — the provider's advertised window (may be ``None``).
    - ``auto_compaction`` — whether the agent loop has compaction armed.

    Args:
        lane: The lane to snapshot.
        git: Latest git facts from a :class:`GitFactsWatcher`, if running.

    Returns:
        A populated :class:`StatusContext`.
    """
    t = lane.telemetry
    state = t.liveness.value
    if t.terminal_reason and t.terminal_reason not in ("completed", None):
        state = f"{state}:{t.terminal_reason}"
    driver = lane.driver
    ws = lane.workspace
    window = getattr(driver, "context_window", None)
    return StatusContext(
        model=lane.config.model,
        reasoning=_maybe_str(getattr(driver, "thinking", None)),
        project_dir=str(ws.path) if ws is not None else None,
        git=git,
        run_state=state,
        busy=t.busy,
        context_used=getattr(t, "context_tokens", 0) or None,
        context_window=int(window) if window else None,
        auto_compaction=bool(getattr(driver, "auto_compaction", False)),
        tokens_in=t.tokens_in,
        tokens_out=t.tokens_out,
        cost=t.cost,
        lanes_total=1,
    )


def build_cohort_context(
    cohort: Any,
    *,
    mode: str | None = None,
    elapsed: float | None = None,
    racing: bool = False,
    git: GitFacts | None = None,
) -> StatusContext:
    """Build the multi-lane context from cohort aggregates.

    Per-lane gauges that have no honest aggregate (context meter, reasoning
    level) are left unavailable; totals (cost, tokens) sum across lanes; the
    model shows only when every lane shares one.

    Args:
        cohort: The :class:`~chimera.tui.cohort.Cohort` to aggregate.
        mode: Routing-mode word (``broadcast``/``targeted``), if any.
        elapsed: Race elapsed seconds; ``None`` before the first race.
        racing: Whether a race has been started this session.
        git: Latest git facts from a :class:`GitFactsWatcher`, if running.

    Returns:
        A populated :class:`StatusContext`.
    """
    lanes = list(getattr(cohort, "lanes", ()))
    models = {lane.config.model for lane in lanes}
    busy = any(lane.telemetry.busy for lane in lanes)
    if not racing:
        state = "idle"
    elif busy:
        state = "running"
    elif any(lane.telemetry.liveness.value == "error" for lane in lanes):
        state = "error"
    else:
        state = "done"
    first = cohort.first_finisher
    return StatusContext(
        model=models.pop() if len(models) == 1 else None,
        project_dir=(getattr(cohort, "source", "") or None),
        git=git,
        run_state=state,
        busy=busy,
        tokens_in=sum(lane.telemetry.tokens_in for lane in lanes),
        tokens_out=sum(lane.telemetry.tokens_out for lane in lanes),
        cost=cohort.total_cost,
        lanes_total=len(lanes),
        lanes_done=cohort.done_count if racing else 0,
        first_label=first.label if first is not None else None,
        task=getattr(cohort, "task", None),
        elapsed=elapsed,
        mode=mode,
        hint=None if racing else "type a task, Enter to race",
    )


def _maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value)) or None


# ---------------------------------------------------------------------------
# Item registry (R-STAT-1)
# ---------------------------------------------------------------------------

Renderer = Callable[[StatusContext], "Text | None"]


@dataclass(frozen=True)
class StatusItem:
    """One named status-line segment.

    Attributes:
        item_id: Stable kebab-case id used in ``tui.status_line``.
        render: Full-form renderer; return ``None`` to hide (unavailable).
        min_width_priority: Degradation rank — *lower degrades first* when the
            terminal narrows (R-STAT-2). Higher-priority items survive longest.
        render_short: Optional compact form used before dropping the item.
        truncate: ``"left"`` if the segment may be `…`-truncated from the left
            (paths), ``"right"`` for free text (task); ``None`` = atomic.
        description: One-liner for the ``/statusline`` listing.
    """

    item_id: str
    render: Renderer
    min_width_priority: int = 50
    render_short: Renderer | None = None
    truncate: str | None = None
    description: str = ""


_REGISTRY: dict[str, StatusItem] = {}


def register_item(item: StatusItem, *, replace: bool = False) -> StatusItem:
    """Register a status item (built-in or third-party).

    Args:
        item: The item to add.
        replace: Allow overwriting an existing id.

    Returns:
        The registered item (for decorator-style chaining).

    Raises:
        ValueError: If the id is already registered and *replace* is false.
    """
    if item.item_id in _REGISTRY and not replace:
        raise ValueError(f"status item {item.item_id!r} already registered")
    _REGISTRY[item.item_id] = item
    return item


def get_item(item_id: str) -> StatusItem | None:
    """Look up a registered item by id."""
    return _REGISTRY.get(item_id)


def all_items() -> tuple[StatusItem, ...]:
    """Every registered item, in registration order."""
    return tuple(_REGISTRY.values())


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_tokens(n: int) -> str:
    """Compact token count: ``950`` → ``950``, ``28_500`` → ``28.5k``,
    ``1_200_000`` → ``1.2M``."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"


def _abbrev_home(path: str) -> str:
    try:
        home = str(Path.home())
    except Exception:  # pragma: no cover - exotic env
        return path
    if home and path.startswith(home):
        return "~" + path[len(home):]
    return path


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


_STATE_STYLES = {
    "running": "yellow",
    "queued": "yellow",
    "error": "bold red",
    "done": "green",
    "idle": "dim",
}


def _state_style(state: str) -> str:
    return _STATE_STYLES.get(state.split(":", 1)[0], "")


# ---------------------------------------------------------------------------
# Built-in items
# ---------------------------------------------------------------------------

def _render_model(ctx: StatusContext) -> Text | None:
    return Text(ctx.model) if ctx.model else None


def _render_model_short(ctx: StatusContext) -> Text | None:
    return Text(_clip(ctx.model, 12)) if ctx.model else None


def _render_reasoning(ctx: StatusContext) -> Text | None:
    return Text(ctx.reasoning, style="dim") if ctx.reasoning else None


def _render_project(ctx: StatusContext) -> Text | None:
    return Text(_abbrev_home(ctx.project_dir), style="dim") if ctx.project_dir else None


def _render_project_short(ctx: StatusContext) -> Text | None:
    if not ctx.project_dir:
        return None
    return Text(Path(ctx.project_dir).name or ctx.project_dir, style="dim")


def _render_git(ctx: StatusContext) -> Text | None:
    facts = ctx.git
    if facts is None:
        return None
    label = facts.branch or (f"@{facts.sha}" if facts.sha else None)
    if not label:
        return None
    if facts.dirty:
        label += "±"
    return Text(label, style="cyan")


def _render_run_state(ctx: StatusContext) -> Text | None:
    if not ctx.run_state:
        return None
    return Text(ctx.run_state, style=_state_style(ctx.run_state))


def _render_context_used(ctx: StatusContext) -> Text | None:
    """The context meter (R-STAT-4) — rendered only from provider-reported
    usage, never estimated."""
    used = ctx.context_used
    if not used or used <= 0:
        return None
    auto = " (auto)" if ctx.auto_compaction else ""
    window = ctx.context_window
    if not window:
        return Text(f"{format_tokens(used)} ctx{auto}")
    ratio = used / window
    style = ""
    if ratio >= _CONTEXT_ERROR:
        style = "bold red"
    elif ratio >= _CONTEXT_WARN:
        style = "yellow"
    return Text(
        f"{ratio * 100:.0f}% · {format_tokens(used)}/{format_tokens(window)}{auto}",
        style=style,
    )


def _render_context_used_short(ctx: StatusContext) -> Text | None:
    used = ctx.context_used
    if not used or used <= 0:
        return None
    window = ctx.context_window
    if not window:
        return Text(f"{format_tokens(used)}ctx")
    ratio = used / window
    style = ""
    if ratio >= _CONTEXT_ERROR:
        style = "bold red"
    elif ratio >= _CONTEXT_WARN:
        style = "yellow"
    return Text(f"{ratio * 100:.0f}%", style=style)


def _render_tokens(ctx: StatusContext) -> Text | None:
    if ctx.tokens_in <= 0 and ctx.tokens_out <= 0:
        return None
    return Text(f"↑{format_tokens(ctx.tokens_in)} ↓{format_tokens(ctx.tokens_out)}")


def _render_tokens_short(ctx: StatusContext) -> Text | None:
    if ctx.tokens_in <= 0 and ctx.tokens_out <= 0:
        return None
    return Text(f"↑{format_tokens(ctx.tokens_in)}↓{format_tokens(ctx.tokens_out)}")


def _render_cost(ctx: StatusContext) -> Text | None:
    if ctx.cost is None:
        return None
    sigma = "Σ" if ctx.lanes_total > 1 else ""
    return Text(f"{sigma}${ctx.cost:.4f}")


def _render_cost_short(ctx: StatusContext) -> Text | None:
    if ctx.cost is None:
        return None
    return Text(f"${ctx.cost:.2f}")


def _render_progress(ctx: StatusContext) -> Text | None:
    if ctx.lanes_total <= 1:
        return None
    if ctx.elapsed is None:  # never raced yet
        return Text(f"lanes {ctx.lanes_total}")
    text = f"done {ctx.lanes_done}/{ctx.lanes_total}"
    if ctx.first_label:
        text += f" · first {ctx.first_label}"
    return Text(text)


def _render_progress_short(ctx: StatusContext) -> Text | None:
    if ctx.lanes_total <= 1:
        return None
    if ctx.elapsed is None:
        return Text(f"{ctx.lanes_total}L")
    return Text(f"{ctx.lanes_done}/{ctx.lanes_total}")


def _render_task(ctx: StatusContext) -> Text | None:
    if not ctx.task:
        return None
    return Text(f"task: {_clip(ctx.task, 40)!r}")


def _render_task_short(ctx: StatusContext) -> Text | None:
    if not ctx.task:
        return None
    return Text(repr(_clip(ctx.task, 16)))


def _render_elapsed(ctx: StatusContext) -> Text | None:
    if ctx.elapsed is None:
        return None
    return Text(f"{ctx.elapsed:.1f}s")


def _render_mode(ctx: StatusContext) -> Text | None:
    return Text(f"[{ctx.mode}]", style="dim") if ctx.mode else None


def _render_hint(ctx: StatusContext) -> Text | None:
    return Text(f"({ctx.hint})", style="dim") if ctx.hint else None


def _render_version(ctx: StatusContext) -> Text | None:
    return Text(f"v{ctx.version}", style="dim") if ctx.version else None


def _register_builtins() -> None:
    for item in (
        StatusItem("model", _render_model, 80, _render_model_short,
                   description="model id"),
        StatusItem("reasoning", _render_reasoning, 65,
                   description="reasoning/thinking level (when the driver exposes one)"),
        StatusItem("project", _render_project, 45, _render_project_short, truncate="left",
                   description="project/cwd path"),
        StatusItem("git", _render_git, 60,
                   description="git branch ± dirty (async watcher)"),
        StatusItem("run-state", _render_run_state, 90,
                   description="turn lifecycle (idle/running/done/error)"),
        StatusItem("context-used", _render_context_used, 75, _render_context_used_short,
                   description="context meter: % + used/window, (auto) when compaction armed"),
        StatusItem("tokens", _render_tokens, 50, _render_tokens_short,
                   description="cumulative token totals ↑in ↓out"),
        StatusItem("cost", _render_cost, 70, _render_cost_short,
                   description="accumulated cost ($; Σ across lanes)"),
        StatusItem("progress", _render_progress, 85, _render_progress_short,
                   description="lane/cohort progress (multi-lane)"),
        StatusItem("task", _render_task, 40, _render_task_short, truncate="right",
                   description="current race task (multi-lane)"),
        StatusItem("elapsed", _render_elapsed, 55,
                   description="race elapsed seconds (multi-lane)"),
        StatusItem("mode", _render_mode, 35,
                   description="routing mode (multi-lane)"),
        StatusItem("hint", _render_hint, 10,
                   description="idle usage hint"),
        StatusItem("version", _render_version, 20,
                   description="chimera version"),
    ):
        register_item(item)


_register_builtins()


# ---------------------------------------------------------------------------
# Width-aware composition (R-STAT-2)
# ---------------------------------------------------------------------------

def compose_status(
    order: Sequence[str],
    ctx: StatusContext,
    width: int,
    *,
    separator: str = _SEPARATOR,
) -> Text:
    """Compose the status line: ordered items, degraded to *width*.

    The ladder (per segment, lowest ``min_width_priority`` first):

    1. presence pass — items rendering ``None`` are omitted outright;
    2. short forms — swap items to ``render_short`` until the line fits;
    3. truncation — `…`-shrink items that declare ``truncate``;
    4. drop — remove whole items;
    5. last resort — hard-truncate whatever single segment remains.

    Unknown ids are skipped (a config typo degrades, never crashes). The
    result is guaranteed to be at most *width* cells wide — the line never
    wraps.

    Args:
        order: Item ids, display order.
        ctx: The snapshot to render.
        width: Usable columns (already minus any widget padding).
        separator: Segment joiner (rendered dim).

    Returns:
        A single-line rich ``Text``.
    """
    width = max(0, width)
    if width == 0:
        return Text()
    segments: list[tuple[StatusItem, Text]] = []
    for item_id in order:
        item = _REGISTRY.get(item_id)
        if item is None:
            continue
        rendered = item.render(ctx)
        if rendered is not None and rendered.plain:
            segments.append((item, rendered))

    sep_len = len(separator)

    def total(segs: list[tuple[StatusItem, Text]]) -> int:
        if not segs:
            return 0
        return sum(len(t.plain) for _, t in segs) + sep_len * (len(segs) - 1)

    # Rung 2: short forms, lowest priority first (ties: rightmost first).
    shortened: set[int] = set()
    while total(segments) > width:
        candidates = [
            (item.min_width_priority, -i, i)
            for i, (item, _) in enumerate(segments)
            if item.render_short is not None and i not in shortened
        ]
        if not candidates:
            break
        _, _, idx = min(candidates)
        item = segments[idx][0]
        short = item.render_short(ctx) if item.render_short else None
        shortened.add(idx)
        if short is not None and short.plain:
            segments[idx] = (item, short)

    # Rung 3: `…`-truncate flexible items (paths from the left, text from the
    # right), lowest priority first, down to a floor that stays legible.
    while total(segments) > width:
        over = total(segments) - width
        candidates = [
            (item.min_width_priority, -i, i)
            for i, (item, t) in enumerate(segments)
            if item.truncate and len(t.plain) > 6
        ]
        if not candidates:
            break
        _, _, idx = min(candidates)
        item, text = segments[idx]
        keep = max(6, len(text.plain) - over)
        plain = text.plain
        if item.truncate == "left":
            segments[idx] = (item, Text("…" + plain[-(keep - 1):], style="dim"))
        else:
            segments[idx] = (item, Text(plain[: keep - 1] + "…"))

    # Rung 4: drop whole items, lowest priority first (ties: rightmost
    # first) — but keep one survivor for rung 5 to truncate, so even a
    # sliver of terminal still shows the highest-priority fact.
    while len(segments) > 1 and total(segments) > width:
        _, _, idx = min(
            (item.min_width_priority, -i, i) for i, (item, _) in enumerate(segments)
        )
        del segments[idx]

    line = Text()
    for i, (_, text) in enumerate(segments):
        if i:
            line.append(separator, style="dim")
        line.append_text(text)

    # Rung 5: a lone oversized segment still must not wrap.
    if len(line.plain) > width:
        line.truncate(width, overflow="ellipsis")
    return line


def parse_item_order(value: Any, default: Sequence[str]) -> tuple[str, ...]:
    """Normalize a config value into an item-id tuple.

    Accepts a list/tuple of ids or a comma-separated string; blank or
    unusable values fall back to *default*. Ids are kept even when unknown —
    :func:`compose_status` skips them and the ``/statusline`` listing can
    flag them.

    Args:
        value: Raw config value (``tui.status_line`` / ``tui.title``).
        default: Fallback order.

    Returns:
        The resolved id order.
    """
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        value = value.split(",")
    if isinstance(value, (list, tuple)):
        ids = [str(v).strip() for v in value if str(v).strip()]
        return tuple(ids) if ids else tuple(default)
    return tuple(default)


# ---------------------------------------------------------------------------
# TUI config (spec §11) — one loader, one precedence chain
# ---------------------------------------------------------------------------


def load_tui_config(
    project_dir: str | os.PathLike[str] | None = None,
    *,
    home: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Load the merged ``tui`` config section.

    Thin adapter over the unified user-config loader
    (:func:`chimera.config.user_config.load_tui_config`): the status line, the
    keybindings, and the skills toggles now all read one config chain. Scopes,
    lowest to highest precedence — ``~/.config/chimera/``, ``~/.chimera/``,
    ``<project>/.chimera/`` — each accepting any ``config.{toml,yaml,yml,json}``
    (``config.toml`` canonical, winning on a key collision), deep-merged so a
    higher scope's ``status_line`` does not erase a lower scope's ``keybinds``.
    Parse errors degrade to an empty scope (a broken config must not take the
    TUI down).

    Args:
        project_dir: Project root (default: cwd).
        home: Home-directory override (tests).

    Returns:
        The ``tui`` section as a dict (``{}`` when absent).
    """
    from chimera.config.user_config import load_tui_config as _load_tui_config

    return _load_tui_config(project_dir, home=home)


# ---------------------------------------------------------------------------
# Async git facts (R-STAT-3)
# ---------------------------------------------------------------------------

class GitFactsWatcher:
    """Background branch/dirty resolver that never blocks a render.

    Branch detection stats the repository ``HEAD`` **by path** every
    *poll_interval* and pairs it with the mtime of HEAD's parent directory:
    git replaces HEAD via write-temp-then-rename, which changes the file's
    inode, so any watch bound to an open handle silently dies after the first
    branch switch — re-resolving the path each tick (and noticing the
    directory-entry change) is immune to that. Dirtiness comes from
    ``git status --porcelain`` run *on this thread only*, refreshed when HEAD
    moves and on a TTL fallback poll (edits don't touch HEAD). Worktree
    layouts (``.git`` file with a ``gitdir:`` pointer) resolve to their
    private gitdir, so per-worktree branches read correctly.

    Renderers call :meth:`snapshot`, a lock-guarded read of the latest
    :class:`GitFacts`; :meth:`stop` shuts the daemon thread down cleanly.

    Args:
        root: Directory inside the repository (the project dir).
        poll_interval: HEAD-stat cadence in seconds (the debounce grain).
        dirty_ttl: Seconds between fallback ``git status`` refreshes.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        poll_interval: float = 0.2,
        dirty_ttl: float = 5.0,
    ) -> None:
        self._root = Path(root)
        self._poll_interval = poll_interval
        self._dirty_ttl = dirty_ttl
        self._git_dir = _resolve_git_dir(self._root)
        self._lock = threading.Lock()
        self._facts: GitFacts | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        """Whether *root* is inside a git repository at all."""
        return self._git_dir is not None

    def start(self) -> None:
        """Read the branch once (fast path) and start the watcher thread."""
        if self._git_dir is None or self._thread is not None:
            return
        self._set_facts(branch_sha=_read_head(self._git_dir))
        self._thread = threading.Thread(
            target=self._run, name="chimera-git-watch", daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the thread and join it (clean shutdown on app exit)."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def snapshot(self) -> GitFacts | None:
        """The latest facts (thread-safe), or ``None`` outside a repo."""
        with self._lock:
            return self._facts

    # -- internals -------------------------------------------------------
    def _set_facts(
        self,
        *,
        branch_sha: tuple[str | None, str | None] | None = None,
        dirty: bool | None = None,
    ) -> None:
        with self._lock:
            prev = self._facts or GitFacts()
            branch, sha = branch_sha if branch_sha is not None else (prev.branch, prev.sha)
            self._facts = GitFacts(
                branch=branch,
                sha=sha,
                dirty=prev.dirty if dirty is None else dirty,
            )

    def _signature(self) -> tuple[int, ...] | None:
        """Inode-aware change signature: parent-dir mtime + fresh HEAD stat."""
        assert self._git_dir is not None
        try:
            d = os.stat(self._git_dir)
            h = os.stat(self._git_dir / "HEAD")
        except OSError:
            return None  # mid-rename or repo vanished; retry next tick
        return (d.st_mtime_ns, h.st_ino, h.st_mtime_ns, h.st_size)

    def _refresh_dirty(self) -> None:
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self._root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return  # dirtiness stays at its last known value
        if proc.returncode == 0:
            self._set_facts(dirty=bool(proc.stdout.strip()))

    def _run(self) -> None:
        assert self._git_dir is not None
        last_sig = self._signature()
        last_dirty = 0.0
        while not self._stop.wait(self._poll_interval):
            now = time.monotonic()
            sig = self._signature()
            head_moved = sig is not None and sig != last_sig
            if head_moved:
                last_sig = sig
                self._set_facts(branch_sha=_read_head(self._git_dir))
            if head_moved or (now - last_dirty) >= self._dirty_ttl:
                last_dirty = now
                self._refresh_dirty()


def _resolve_git_dir(start: Path) -> Path | None:
    """Find the gitdir holding HEAD for *start* (worktree-aware), or None."""
    try:
        current = start.resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        dot_git = candidate / ".git"
        if dot_git.is_dir():
            return dot_git
        if dot_git.is_file():
            # Worktree / submodule pointer: "gitdir: <path>" (maybe relative).
            try:
                text = dot_git.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
            for line in text.splitlines():
                if line.startswith("gitdir:"):
                    target = Path(line.split(":", 1)[1].strip())
                    if not target.is_absolute():
                        target = (candidate / target).resolve()
                    return target if target.is_dir() else None
            return None
    return None


def _read_head(git_dir: Path) -> tuple[str | None, str | None]:
    """Fast-path HEAD read: (branch, short-sha-when-detached)."""
    try:
        text = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return (None, None)
    if text.startswith("ref:"):
        ref = text.split(":", 1)[1].strip()
        prefix = "refs/heads/"
        branch = ref[len(prefix):] if ref.startswith(prefix) else ref
        return (branch or None, None)
    if text:
        return (None, text[:7])
    return (None, None)


# ---------------------------------------------------------------------------
# Terminal title (R-STAT-5)
# ---------------------------------------------------------------------------

# Bidi/direction-control characters are printable per str.isprintable() but
# must never reach a terminal title.
_TITLE_FORBIDDEN = frozenset({
    "\u061c",  # arabic letter mark
    "\u200e", "\u200f",  # LRM / RLM
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",  # LRE RLE PDF LRO RLO
    "\u2066", "\u2067", "\u2068", "\u2069",  # LRI RLI FSI PDI
})
_TITLE_MAX = 120


def sanitize_title(text: str) -> str:
    """Strip control and bidi characters and cap the length for a terminal
    title (R-STAT-5)."""
    cleaned = "".join(
        ch for ch in text
        if (ch == " " or ch.isprintable()) and ch not in _TITLE_FORBIDDEN
    )
    return cleaned[:_TITLE_MAX].strip()


TitleRenderer = Callable[[StatusContext], "str | None"]

TITLE_ITEMS: dict[str, TitleRenderer] = {
    "activity": lambda ctx: "✳" if ctx.busy else None,
    "project": lambda ctx: (Path(ctx.project_dir).name or None) if ctx.project_dir else None,
    "model": lambda ctx: ctx.model,
    "task": lambda ctx: _clip(ctx.task, 40) if ctx.task else None,
    "run-state": lambda ctx: ctx.run_state,
}


def compose_title(order: Sequence[str], ctx: StatusContext) -> str:
    """Compose the terminal-title string from ordered title items,
    sanitized; unknown ids are skipped."""
    parts: list[str] = []
    for item_id in order:
        renderer = TITLE_ITEMS.get(item_id)
        if renderer is None:
            continue
        value = renderer(ctx)
        if value:
            parts.append(value)
    return sanitize_title(" ".join(parts))


class TerminalTitle:
    """Sets the terminal-emulator title, restoring the prior one on exit.

    Textual's title mechanism (``App.title``/``sub_title``) only feeds the
    in-app Header — it never emits emulator escapes — so this writes OSC 0
    directly. There is no portable way to *read* the current title, so the
    xterm title stack is used instead: pushed before the first write and
    popped on :meth:`restore`. Off-TTY (tests, pipes) every call is a no-op.

    Args:
        stream: Byte-text stream to write escapes to; defaults to the real
            stdout (``sys.__stdout__``).
        enabled: Force on/off; default = whether *stream* is a TTY.
    """

    _PUSH = "\x1b[22;0t"
    _POP = "\x1b[23;0t"

    def __init__(self, stream: Any = None, *, enabled: bool | None = None) -> None:
        self._stream = stream if stream is not None else sys.__stdout__
        if enabled is None:
            try:
                enabled = bool(self._stream is not None and self._stream.isatty())
            except Exception:
                enabled = False
        self._enabled = enabled
        self._pushed = False
        self._last: str | None = None

    def apply(self, title: str) -> None:
        """Set the title (sanitized, deduplicated; pushes the stack once)."""
        if not self._enabled:
            return
        title = sanitize_title(title)
        if not title or title == self._last:
            return
        out = ""
        if not self._pushed:
            out += self._PUSH
            self._pushed = True
        out += f"\x1b]0;{title}\x07"
        self._write(out)
        self._last = title

    def restore(self) -> None:
        """Pop the title stack, restoring whatever title preceded us."""
        if self._enabled and self._pushed:
            self._write(self._POP)
            self._pushed = False
            self._last = None

    def _write(self, data: str) -> None:
        stream = self._stream
        if stream is None:  # sys.__stdout__ can be None in embedded runs
            self._enabled = False
            return
        try:
            stream.write(data)
            stream.flush()
        except Exception:
            self._enabled = False  # broken pipe etc.: stop trying


# ---------------------------------------------------------------------------
# Frontend glue — one object the app owns
# ---------------------------------------------------------------------------

class StatusLine:
    """The status-line + title state a TUI app owns (config, watcher, title).

    Groups everything so a frontend's footprint stays tiny: construct with
    the project dir, ``start()`` on mount, ``render(ctx, width)`` for the
    bar, ``apply_title(ctx, app)`` on the refresh tick, ``stop()`` on exit.

    The git watcher only spawns when the configured order actually shows the
    ``git`` item (or the title needs activity from it) — no subprocess churn
    for the default layout.

    Args:
        project_dir: Repository/project root for git + config discovery.
        single: Single-lane (daily driver) or multi-lane default order.
        config: Pre-loaded ``tui`` config section; ``None`` loads it from
            the standard scopes via :func:`load_tui_config`.
        title_stream: Escape-sequence stream override (tests).
    """

    def __init__(
        self,
        project_dir: str | None = None,
        *,
        single: bool = True,
        config: dict[str, Any] | None = None,
        title_stream: Any = None,
    ) -> None:
        self._project_dir = project_dir or os.getcwd()
        if config is None:
            try:
                config = load_tui_config(self._project_dir)
            except Exception:
                config = {}
        default = DEFAULT_STATUS_LINE if single else DEFAULT_STATUS_LINE_MULTI
        self.order: tuple[str, ...] = parse_item_order(config.get("status_line"), default)
        raw_title = config.get("title")
        self.title_enabled = raw_title not in (False, "off", "false")
        self.title_order: tuple[str, ...] = parse_item_order(
            raw_title if self.title_enabled else None, DEFAULT_TITLE,
        )
        self._watcher = GitFactsWatcher(self._project_dir)
        self._title = TerminalTitle(stream=title_stream)

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        """Start async fact resolution (only what the layout needs)."""
        if "git" in self.order:
            self._watcher.start()

    def stop(self) -> None:
        """Stop the watcher thread and restore the terminal title."""
        self._watcher.stop()
        self._title.restore()

    # -- data ------------------------------------------------------------
    def git_facts(self) -> GitFacts | None:
        """Latest watcher snapshot (never blocks)."""
        return self._watcher.snapshot()

    # -- rendering -------------------------------------------------------
    def render(self, ctx: StatusContext, width: int) -> Text:
        """Compose the configured status line for *width* columns."""
        return compose_status(self.order, ctx, width)

    def apply_title(self, ctx: StatusContext, app: Any = None) -> str:
        """Compose + apply the terminal title (R-STAT-5).

        Sets Textual's ``sub_title`` (the Header's state slot) when *app* is
        given and emits the OSC title for the real emulator.

        Returns:
            The composed (sanitized) title, for tests/logging.
        """
        if not self.title_enabled:
            return ""
        title = compose_title(self.title_order, ctx)
        if app is not None and title and getattr(app, "sub_title", None) != title:
            app.sub_title = title
        if title:
            self._title.apply(title)
        return title

    def describe(self, ctx: StatusContext) -> list[str]:
        """``/statusline`` listing: order, every item, availability.

        One row per registered item — position in the current order (or
        ``off``), and whether it currently renders or hides. Interactive
        reorder/multi-select arrives with the shared select-dialog component;
        until then the rows point at the config key.

        Args:
            ctx: The live context to evaluate availability against.

        Returns:
            Display lines for the transcript.
        """
        lines = [f"status line: {' → '.join(self.order)}   (tui.status_line)"]
        position = {item_id: i + 1 for i, item_id in enumerate(self.order)}
        for item in all_items():
            slot = f"#{position[item.item_id]}" if item.item_id in position else "off"
            rendered = item.render(ctx)
            state = "shown" if rendered is not None and rendered.plain else "hidden (no data)"
            if item.item_id in position and rendered is None:
                state = "hidden (no data yet)"
            lines.append(f"  {item.item_id:<13} {slot:>4}  {state:<18} {item.description}")
        unknown = [i for i in self.order if i not in _REGISTRY]
        if unknown:
            lines.append(f"  (unknown ids ignored: {', '.join(unknown)})")
        lines.append(
            "reorder via tui.status_line in .chimera/config.yaml — "
            "interactive picker lands with the select dialog"
        )
        return lines

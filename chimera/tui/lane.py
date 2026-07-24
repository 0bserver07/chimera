"""Lane model + telemetry for the multiplexer (spec §6.1, §6.5).

A **lane** is one agent session reduced to a pane: its own driver (anything
satisfying :class:`~chimera.assembly.driver.DriverProtocol` — an
:class:`~chimera.assembly.driver.AgentDriver`, or an
:class:`~chimera.assembly.external_driver.ExternalAgentDriver` wrapping a real
third-party CLI), its own isolated workspace, and its own telemetry. Lanes
share nothing mutable (R-ISO-2/3), which is what makes side-by-side comparison
sound.

This module is presentation-agnostic: it folds a driver's ``LoopEvent`` stream
into a :class:`LaneTelemetry` snapshot and a plain-text transcript. The
multiplexer app renders these; persistence/export reads them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from chimera.core.loop_events import LoopEventType
from chimera.tui.render import format_event, plain

if TYPE_CHECKING:
    from chimera.assembly.driver import DriverProtocol
    from chimera.tui.workspace import LaneWorkspace

__all__ = ["Liveness", "LaneTelemetry", "LaneConfig", "Lane"]


class Liveness(Enum):
    """Where a lane is in its turn lifecycle."""

    IDLE = "idle"        # constructed, no turn yet run
    QUEUED = "queued"    # waiting for a concurrency slot
    RUNNING = "running"  # a turn is streaming
    DONE = "done"        # finished a turn cleanly; ready for another
    ERROR = "error"      # last turn ended in error


# Liveness values that mean "a turn is in flight" (for routing / done-counts).
_BUSY = frozenset({Liveness.QUEUED, Liveness.RUNNING})


@dataclass
class LaneTelemetry:
    """Live per-lane comparison metrics (§6.5)."""

    cost: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    #: Prompt-side token count of the *latest* provider request (observed from
    #: per-step ``assistant`` events) — the live context-usage gauge for the
    #: status line (R-STAT-4). 0 until a provider reports usage; never
    #: estimated locally.
    context_tokens: int = 0
    steps: int = 0
    turns: int = 0
    elapsed: float = 0.0            # cumulative seconds spent running
    liveness: Liveness = Liveness.IDLE
    terminal_reason: str | None = None
    finished_order: int | None = None  # 1 = first to finish the current race

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    @property
    def busy(self) -> bool:
        return self.liveness in _BUSY


def _usage_tokens(usage: dict[str, Any], *keys: str) -> int:
    for k in keys:
        if k in usage and usage[k] is not None:
            try:
                return int(usage[k])
            except (TypeError, ValueError):
                return 0
    return 0


@dataclass
class LaneConfig:
    """A lane's controlled variables — recorded in the cohort manifest (R-ISO-5)."""

    lane_id: str
    label: str
    model: str
    preset: str = "coding_agent"
    loop: str | None = None  # reserved: per-lane loop override

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "label": self.label,
            "model": self.model,
            "preset": self.preset,
            "loop": self.loop,
        }


class Lane:
    """One agent session in the cohort: a driver + workspace + telemetry.

    The lane does not render; it *accumulates*. :meth:`record` folds each event
    into telemetry and a plain-text transcript. A frontend renders the same
    events its own way; persistence reads :meth:`transcript_text` and the
    workspace diff.
    """

    def __init__(
        self,
        config: LaneConfig,
        driver: DriverProtocol,
        workspace: LaneWorkspace | None = None,
    ) -> None:
        self.config = config
        self.driver = driver
        self.workspace = workspace
        self.telemetry = LaneTelemetry()
        self.transcript_lines: list[str] = []
        # Tool-call timeline for the sidebar (§13.7): (name, ok) — ok is None
        # while the call is in flight, then True/False from its result.
        self.tool_log: list[tuple[str, bool | None]] = []
        self._text_chunks: list[str] = []
        self._turn_started: float | None = None

    @property
    def id(self) -> str:
        return self.config.lane_id

    @property
    def label(self) -> str:
        return self.config.label

    # -- lifecycle ------------------------------------------------------
    def mark_queued(self) -> None:
        """A turn has been requested but is waiting for a concurrency slot."""
        self.telemetry.liveness = Liveness.QUEUED

    def on_turn_begin(self) -> None:
        """A turn has acquired its slot and is now streaming."""
        self.telemetry.liveness = Liveness.RUNNING
        self._turn_started = time.monotonic()

    def on_turn_end(self, *, order: int | None = None) -> None:
        """A turn has ended (cleanly, cancelled, or errored)."""
        if self._turn_started is not None:
            self.telemetry.elapsed += time.monotonic() - self._turn_started
            self._turn_started = None
        self.telemetry.liveness = (
            Liveness.ERROR if self.telemetry.terminal_reason == "error" else Liveness.DONE
        )
        if order is not None and self.telemetry.finished_order is None:
            self.telemetry.finished_order = order

    def reset_race(self) -> None:
        """Clear per-race markers before a fresh broadcast."""
        self.telemetry.finished_order = None
        self.telemetry.terminal_reason = None

    # -- event folding --------------------------------------------------
    def record(self, ev: Any) -> None:
        """Fold one event into telemetry and the plain-text transcript."""
        for renderable in format_event(ev, self._text_chunks):
            self.transcript_lines.append(plain(renderable))
        self._observe(ev)

    def _observe(self, ev: Any) -> None:
        if ev.type == LoopEventType.assistant:
            # Per-step responses carry the usage of that single request; its
            # prompt side (fresh input + cache read/write for providers that
            # split them out) is the real size of the context as last sent.
            # The turn-end ``result`` usage is cumulative across steps and
            # deliberately NOT used here.
            usage = getattr(ev.data, "usage", None) or {}
            prompt_side = (
                _usage_tokens(usage, "input_tokens", "prompt_tokens")
                + _usage_tokens(usage, "cache_read_input_tokens")
                + _usage_tokens(usage, "cache_creation_input_tokens")
            )
            if prompt_side > 0:
                self.telemetry.context_tokens = prompt_side
        if ev.type == LoopEventType.tool_use:
            self.tool_log.append((str(getattr(ev.data, "name", "?")), None))
        elif ev.type == LoopEventType.tool_result:
            _, result = ev.data if isinstance(ev.data, tuple) else (None, ev.data)
            ok = bool(getattr(result, "success", True))
            for i in range(len(self.tool_log) - 1, -1, -1):
                if self.tool_log[i][1] is None:
                    self.tool_log[i] = (self.tool_log[i][0], ok)
                    break
        if ev.type == LoopEventType.result:
            r = ev.data
            self.telemetry.turns += 1
            self.telemetry.steps += int(getattr(r, "turn_count", 0) or 0)
            self.telemetry.cost += float(getattr(r, "cost_usd", 0.0) or 0.0)
            usage = getattr(r, "usage", None) or {}
            self.telemetry.tokens_in += _usage_tokens(usage, "input_tokens", "prompt_tokens")
            self.telemetry.tokens_out += _usage_tokens(usage, "output_tokens", "completion_tokens")
            self.telemetry.terminal_reason = getattr(r, "reason", None)
        elif ev.type == LoopEventType.error:
            self.telemetry.terminal_reason = "error"

    def note(self, text: str) -> None:
        """Record a frontend-originated transcript line (steer marker, echo)."""
        self.transcript_lines.append(text)

    def transcript_text(self) -> str:
        """The lane's full transcript as plain text (for persistence)."""
        if self._text_chunks:
            self.transcript_lines.append("".join(self._text_chunks))
            self._text_chunks = []
        return "\n".join(self.transcript_lines)

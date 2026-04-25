#!/usr/bin/env python3
"""PostToolUse hook: detect and break agent loops.

Integrates with Chimera's detection subsystem (ExactRepeatDetector,
PatternCycleDetector) and exposes the detector as both a Python module
and a hook script invokable by a compatible coding-agent harness.

When run as a hook, reads tool input from stdin (JSON) and tracks commands
in a file-backed history.  When used as a module, the ``LoopDetectorHook``
class can be wired into an :class:`~chimera.events.base.EventBus`.

Exit codes (hook mode):
    0 — no loop detected (or loop detected with steering message).

Output on stdout is relayed to the agent so it receives the nudge.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from chimera.detection.base import DetectionResult
from chimera.detection.exact import ExactRepeatDetector
from chimera.detection.pattern import PatternCycleDetector

__all__ = ["LoopDetectorHook", "LoopEvent"]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class LoopEvent:
    """Record emitted when a loop is detected.

    Attributes:
        pattern: Human-readable description of the detected pattern.
        strategy: Name of the strategy that fired.
        nudge: Steering message injected to break the loop.
        timestamp: Monotonic timestamp of detection.
    """

    pattern: str
    strategy: str
    nudge: str
    timestamp: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Core hook class
# ---------------------------------------------------------------------------

class LoopDetectorHook:
    """Detect and break agent command loops.

    Tracks tool calls in a sliding window using Chimera's
    :class:`~chimera.detection.exact.ExactRepeatDetector` (consecutive
    identical commands) and
    :class:`~chimera.detection.pattern.PatternCycleDetector` (A-B-A-B
    cycles).

    When a loop is detected the hook returns a *nudge* message that
    should be injected into the conversation to steer the agent away
    from the repetitive behaviour.

    Args:
        window: Sliding-window size for detection history.
        repeat_threshold: How many consecutive identical calls trigger
            exact-repeat detection.
        cycle_threshold: How many repetitions of a cycle pattern trigger
            cycle detection.
        nudge_exact: Message injected on exact-repeat detection.
        nudge_cycle: Message injected on cycle detection.
        on_detect: Optional callback invoked with a :class:`LoopEvent`
            whenever a loop is detected.

    Example::

        hook = LoopDetectorHook()
        nudge = hook.record("bash", {"command": "ls"})
        nudge = hook.record("bash", {"command": "ls"})
        nudge = hook.record("bash", {"command": "ls"})
        assert nudge is not None  # 3 identical commands
    """

    DEFAULT_NUDGE_EXACT = (
        "You appear to be repeating the same command. "
        "Try a different approach to make progress."
    )
    DEFAULT_NUDGE_CYCLE = (
        "A circular pattern has been detected in your tool calls. "
        "Step back, re-evaluate the problem, and try a fundamentally "
        "different strategy."
    )

    def __init__(
        self,
        window: int = 10,
        repeat_threshold: int = 3,
        cycle_threshold: int = 2,
        nudge_exact: str | None = None,
        nudge_cycle: str | None = None,
        on_detect: Callable[[LoopEvent], None] | None = None,
    ) -> None:
        self._exact = ExactRepeatDetector(
            window=window, threshold=repeat_threshold,
        )
        self._cycle = PatternCycleDetector(
            window=window, threshold=cycle_threshold,
        )
        self._nudge_exact = nudge_exact or self.DEFAULT_NUDGE_EXACT
        self._nudge_cycle = nudge_cycle or self.DEFAULT_NUDGE_CYCLE
        self._on_detect = on_detect
        self._history: list[LoopEvent] = []

    # -- public API ----------------------------------------------------------

    def record(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> str | None:
        """Record a tool call and check for loops.

        Args:
            tool_name: Name of the tool that was invoked.
            args: Tool arguments dict.

        Returns:
            A nudge/steering message if a loop was detected, otherwise
            ``None``.
        """
        args = args or {}

        # Feed both detectors
        self._exact.record(tool_name, args)
        self._cycle.record(tool_name, args)

        # Check exact repeat first (higher priority)
        result = self._exact.check()
        if result is not None:
            return self._emit(result, self._nudge_exact)

        # Check pattern cycle
        result = self._cycle.check()
        if result is not None:
            return self._emit(result, self._nudge_cycle)

        return None

    def reset(self) -> None:
        """Clear all detection state."""
        self._exact.reset()
        self._cycle.reset()

    @property
    def events(self) -> list[LoopEvent]:
        """All loop-detection events emitted so far."""
        return list(self._history)

    @property
    def loop_count(self) -> int:
        """Number of loops detected so far."""
        return len(self._history)

    # -- EventBus integration ------------------------------------------------

    def attach(self, event_bus: Any) -> None:
        """Subscribe to ToolCallEvent on a Chimera EventBus.

        Args:
            event_bus: An :class:`~chimera.events.base.EventBus` instance.
        """
        event_bus.subscribe("tool_call", self._handle_tool_call_event)

    def _handle_tool_call_event(self, event: Any) -> None:
        """Handle a ToolCallEvent from the EventBus."""
        tool_name = getattr(event, "tool_name", "")
        arguments = getattr(event, "arguments", {})
        self.record(tool_name, arguments)

    # -- internals -----------------------------------------------------------

    def _emit(self, result: DetectionResult, nudge: str) -> str:
        """Record a detection event and return the nudge."""
        event = LoopEvent(
            pattern=result.pattern,
            strategy=result.strategy,
            nudge=nudge,
        )
        self._history.append(event)
        if self._on_detect:
            self._on_detect(event)
        return nudge


# ---------------------------------------------------------------------------
# Hook entry point (invoked by a compatible coding-agent harness)
# ---------------------------------------------------------------------------

_STATE_FILE = os.path.expanduser("~/.chimera/loop_detector_state.json")


def _load_state() -> list[dict[str, Any]]:
    """Load command history from state file."""
    try:
        with open(_STATE_FILE) as f:
            loaded: list[dict[str, Any]] = json.load(f)
            return loaded
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_state(history: list[dict[str, Any]]) -> None:
    """Save command history to state file."""
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    # Keep only last 20 entries
    with open(_STATE_FILE, "w") as f:
        json.dump(history[-20:], f)


def _read_input() -> dict[str, Any]:
    """Read tool input from stdin or TOOL_INPUT env var."""
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                parsed: dict[str, Any] = json.loads(raw)
                return parsed
        except (json.JSONDecodeError, OSError):
            pass

    env_val = os.environ.get("TOOL_INPUT", "")
    if env_val:
        try:
            parsed_env: dict[str, Any] = json.loads(env_val)
            return parsed_env
        except json.JSONDecodeError:
            pass

    return {}


def handle(tool_input: dict[str, Any]) -> str:
    """Handle a PostToolUse event for loop detection.

    Args:
        tool_input: Parsed tool input JSON from the harness.

    Returns:
        Steering message if loop detected, empty string otherwise.
    """
    tool_name = tool_input.get("tool_name", "")
    arguments = tool_input.get("tool_input", tool_input)

    if not tool_name:
        return ""

    hook = LoopDetectorHook()

    # Replay recent history to rebuild detector state
    history = _load_state()
    for entry in history:
        hook.record(entry.get("tool_name", ""), entry.get("args", {}))

    # Record current call
    nudge = hook.record(tool_name, arguments)

    # Persist
    history.append({"tool_name": tool_name, "args": arguments})
    _save_state(history)

    return nudge or ""


def main() -> None:
    """Entry point for the hook script."""
    tool_input = _read_input()
    if not tool_input:
        sys.exit(0)

    result = handle(tool_input)
    if result:
        print(result)

    sys.exit(0)


if __name__ == "__main__":
    main()

"""High-level facade and action policy for loop detection."""
from __future__ import annotations

from enum import Enum
from typing import Any

from chimera.detection.base import DetectionResult, DetectionStrategy
from chimera.detection.composite import CompositeDetector
from chimera.detection.exact import ExactRepeatDetector
from chimera.detection.pattern import PatternCycleDetector

__all__ = ["OnDetect", "LoopDetector"]


class OnDetect(Enum):
    """Action to take when a loop is detected.

    Attributes:
        ASK: Prompt the user for confirmation before continuing.
        BREAK: Immediately stop the agent loop.
        WARN: Log a warning but continue execution.
    """

    ASK = "ask"
    BREAK = "break"
    WARN = "warn"


class LoopDetector:
    """Convenience facade that wires together detection strategies.

    When *strategies* is ``None`` (the default), a
    :class:`~chimera.detection.composite.CompositeDetector` containing
    :class:`~chimera.detection.exact.ExactRepeatDetector` and
    :class:`~chimera.detection.pattern.PatternCycleDetector` is created
    automatically.

    Parameters:
        strategies: Explicit list of strategies, or ``None`` for defaults.
        on_detect: What to do when a loop is detected.
        window: Sliding-window size forwarded to the default strategies.
        threshold: Repetition threshold forwarded to the default strategies.
    """

    def __init__(
        self,
        strategies: list[DetectionStrategy] | None = None,
        on_detect: OnDetect = OnDetect.WARN,
        window: int = 10,
        threshold: int = 3,
    ) -> None:
        self.on_detect = on_detect
        if strategies is not None:
            self._detector = CompositeDetector(strategies)
        else:
            self._detector = CompositeDetector([
                ExactRepeatDetector(window=window, threshold=threshold),
                PatternCycleDetector(window=window, threshold=threshold),
            ])

    def record_and_check(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> DetectionResult | None:
        """Record a tool call and immediately check for loops.

        Returns:
            A :class:`~chimera.detection.base.DetectionResult` when a loop is
            found, otherwise ``None``.
        """
        self._detector.record(tool_name, args)
        return self._detector.check()

    def reset(self) -> None:
        """Clear all internal state across every strategy."""
        self._detector.reset()

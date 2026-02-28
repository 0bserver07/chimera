"""Pattern-cycle detection strategy (migrated from ``chimera.core.loop_detection``)."""
from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any

from chimera.detection.base import DetectionResult, DetectionStrategy

__all__ = ["PatternCycleDetector"]


class PatternCycleDetector(DetectionStrategy):
    """Detects repeating A-B-A-B (or longer period) cycles in tool-call history.

    The algorithm checks every candidate period from 2 up to ``len(history) // 2``.
    A cycle is confirmed when the same sub-sequence repeats *threshold* times
    consecutively at the tail of the history.

    Parameters:
        window: Maximum number of recent signatures to retain.
        threshold: How many consecutive repetitions of the cycle constitute a match.
    """

    def __init__(self, window: int = 10, threshold: int = 2) -> None:
        self.window = window
        self.threshold = threshold
        self._history: deque[str] = deque(maxlen=window)

    # -- DetectionStrategy interface -----------------------------------------

    def record(self, tool_name: str, args: dict[str, Any]) -> None:  # noqa: D401
        """Append a tool-call signature to the sliding window."""
        self._history.append(self._signature(tool_name, args))

    def check(self) -> DetectionResult | None:
        """Return a result if a repeating cycle is found at the tail."""
        items = list(self._history)
        for period in range(2, len(items) // 2 + 1):
            required = period * self.threshold
            if len(items) < required:
                continue
            tail = items[-required:]
            base = tail[:period]
            if all(
                tail[i * period : (i + 1) * period] == base
                for i in range(1, self.threshold)
            ):
                return DetectionResult(
                    detected=True,
                    strategy="pattern_cycle",
                    pattern=(
                        f"Cycle of period {period} repeated "
                        f"{self.threshold} times"
                    ),
                )
        return None

    def reset(self) -> None:
        self._history.clear()

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _signature(tool_name: str, args: dict[str, Any]) -> str:
        """Create an MD5 hex-digest signature for a tool call."""
        raw = json.dumps({"name": tool_name, "args": args}, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()

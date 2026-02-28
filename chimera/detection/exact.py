"""Exact-repeat detection strategy (migrated from ``chimera.core.loop_detection``)."""
from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any

from chimera.detection.base import DetectionResult, DetectionStrategy

__all__ = ["ExactRepeatDetector"]


class ExactRepeatDetector(DetectionStrategy):
    """Detects when the last *threshold* tool calls share the same MD5 signature.

    Parameters:
        window: Maximum number of recent signatures to retain.
        threshold: How many consecutive identical signatures trigger detection.
    """

    def __init__(self, window: int = 10, threshold: int = 3) -> None:
        self.window = window
        self.threshold = threshold
        self._history: deque[str] = deque(maxlen=window)

    # -- DetectionStrategy interface -----------------------------------------

    def record(self, tool_name: str, args: dict[str, Any]) -> None:  # noqa: D401
        """Append a tool-call signature to the sliding window."""
        self._history.append(self._signature(tool_name, args))

    def check(self) -> DetectionResult | None:
        """Return a result if the last *threshold* signatures are identical."""
        if len(self._history) < self.threshold:
            return None
        tail = list(self._history)[-self.threshold:]
        if len(set(tail)) == 1:
            return DetectionResult(
                detected=True,
                strategy="exact_repeat",
                pattern=(
                    f"Same tool call repeated {self.threshold} times "
                    f"(sig={tail[0][:8]}...)"
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

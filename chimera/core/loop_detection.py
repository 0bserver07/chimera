# chimera/core/loop_detection.py
# NOTE: For new code prefer ``chimera.detection`` which provides a richer API
# (pluggable strategies, DetectionResult dataclass, OnDetect policy enum).
# This module is kept for backwards compatibility.
from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any


class LoopDetector:
    """Detects when an agent is stuck in a loop.

    Tracks recent tool calls and detects:
    1. Exact repetition (same call N times)
    2. Pattern repetition (A-B-A-B cycle)
    """

    def __init__(self, window: int = 10, threshold: int = 3) -> None:
        self.window = window
        self.threshold = threshold
        self._history: deque[str] = deque(maxlen=window)

    def record(self, tool_name: str, args: dict[str, Any]) -> None:
        """Record a tool call."""
        sig = self._signature(tool_name, args)
        self._history.append(sig)

    def is_looping(self) -> bool:
        """Check if the agent is stuck in a loop."""
        if len(self._history) < self.threshold:
            return False

        items = list(self._history)

        # Check 1: Same call repeated N times at the tail
        if len(set(items[-self.threshold:])) == 1:
            return True

        # Check 2: Repeating pattern (period 1 to window//2)
        for period in range(2, len(items) // 2 + 1):
            if len(items) >= period * self.threshold:
                # Extract the last `period * threshold` items
                tail = items[-(period * self.threshold):]
                pattern = tail[:period]
                repeats = True
                for i in range(1, self.threshold):
                    chunk = tail[i * period:(i + 1) * period]
                    if chunk != pattern:
                        repeats = False
                        break
                if repeats:
                    return True

        return False

    def reset(self) -> None:
        self._history.clear()

    @staticmethod
    def _signature(tool_name: str, args: dict[str, Any]) -> str:
        """Create a hash signature for a tool call."""
        raw = json.dumps({"name": tool_name, "args": args}, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()

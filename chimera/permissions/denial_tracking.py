"""Track repeated denials to auto-deny after a threshold."""
from __future__ import annotations

from collections import defaultdict

__all__ = ["DenialTrackingState"]


class DenialTrackingState:
    """Counts consecutive denials per (tool_name, content) pair.

    After *max_denials* denials for the same pair the checker can
    skip the prompt and auto-deny.

    Parameters:
        max_denials: Number of denials before auto-deny kicks in.
    """

    def __init__(self, max_denials: int = 3) -> None:
        self._max_denials = max_denials
        self._counts: dict[tuple[str, str | None], int] = defaultdict(int)

    def record_denial(self, tool_name: str, content: str | None = None) -> None:
        """Record a single denial for *tool_name* (optionally with *content*)."""
        key = (tool_name, content)
        self._counts[key] += 1

    def should_auto_deny(self, tool_name: str, content: str | None = None) -> bool:
        """Return ``True`` if the denial count has reached *max_denials*."""
        key = (tool_name, content)
        return self._counts[key] >= self._max_denials

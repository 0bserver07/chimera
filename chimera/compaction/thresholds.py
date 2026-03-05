"""Hard/soft threshold compaction with atomicity awareness."""
from __future__ import annotations

from chimera.compaction.base import (
    CompactionStrategy,
    CompactionUrgency,
    CompactionView,
)
from chimera.types import Message


class InsufficientCompactionError(Exception):
    """Raised when compaction cannot free enough space."""


class ThresholdCompaction:
    """Compaction with hard/soft thresholds for graceful degradation.

    Args:
        strategy: Underlying compaction strategy for selecting messages to remove.
        soft_threshold: Context usage ratio to trigger soft compaction.
        hard_threshold: Context usage ratio to trigger emergency reset.
        max_context_tokens: Maximum context window size in tokens.
        keep_last: Number of tail messages to keep during hard reset.
    """

    def __init__(
        self,
        strategy: CompactionStrategy,
        soft_threshold: float = 0.7,
        hard_threshold: float = 0.9,
        max_context_tokens: int = 128000,
        keep_last: int = 5,
    ) -> None:
        self.strategy = strategy
        self.soft_threshold = soft_threshold
        self.hard_threshold = hard_threshold
        self.max_context_tokens = max_context_tokens
        self.keep_last = keep_last

    def check_urgency(self, view: CompactionView) -> CompactionUrgency:
        """Determine how urgently compaction is needed.

        Args:
            view: Current context view.

        Returns:
            The urgency level based on token usage vs thresholds.
        """
        if self.max_context_tokens == 0:
            return CompactionUrgency.NONE
        ratio = view.token_estimate / self.max_context_tokens
        if ratio >= self.hard_threshold:
            return CompactionUrgency.HARD
        if ratio >= self.soft_threshold:
            return CompactionUrgency.SOFT
        return CompactionUrgency.NONE

    def compact(self, view: CompactionView) -> CompactionView:
        """Apply compaction with atomicity awareness.

        Args:
            view: Current context view.

        Returns:
            A compacted :class:`CompactionView`, or the original if no
            compaction was needed.
        """
        urgency = self.check_urgency(view)

        if urgency == CompactionUrgency.NONE:
            return view

        try:
            return self._compact_safe(view)
        except InsufficientCompactionError:
            if urgency == CompactionUrgency.SOFT:
                return view
            else:
                return self._hard_reset(view)

    def _compact_safe(self, view: CompactionView) -> CompactionView:
        """Compact using strategy, only at safe indices."""
        safe = view.safe_removal_indices
        if not safe:
            raise InsufficientCompactionError("No safe removal points")

        # Use underlying strategy on the full message list, then filter
        # to only safe removal indices
        budget = int(self.max_context_tokens * self.soft_threshold)
        compacted_messages = self.strategy.compact(view.messages, budget)

        if len(compacted_messages) >= len(view.messages):
            raise InsufficientCompactionError("Strategy found nothing to remove")

        return CompactionView(compacted_messages)

    def _hard_reset(self, view: CompactionView) -> CompactionView:
        """Emergency: keep system prompt + last N messages."""
        messages: list[Message] = []

        if view.messages and view.messages[0].role == "system":
            messages.append(view.messages[0])

        messages.append(Message.system(
            "[Previous context was compressed due to length. "
            "Recent conversation follows.]"
        ))

        tail = view.messages[-self.keep_last:]
        messages.extend(tail)

        return CompactionView(messages)

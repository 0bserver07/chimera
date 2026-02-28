from __future__ import annotations

from chimera.types import Message

from chimera.compaction.base import CompactionStrategy
from chimera.compaction.counter import TokenCounter


class CompositeCompaction(CompactionStrategy):
    """Chain multiple compaction strategies, stopping once within budget.

    Strategies are applied in the order supplied.  After each strategy the
    token count is re-evaluated and the pipeline short-circuits as soon as
    the result fits within *budget*.
    """

    def __init__(self, strategies: list[CompactionStrategy]) -> None:
        self._strategies = strategies
        self._counter = TokenCounter()

    def compact(self, messages: list[Message], budget: int) -> list[Message]:
        """Apply strategies sequentially until within *budget*."""
        current = messages
        for strategy in self._strategies:
            if self._counter.count_messages(current) <= budget:
                break
            current = strategy.compact(current, budget)
        return current

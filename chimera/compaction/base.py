from __future__ import annotations

from abc import ABC, abstractmethod

from chimera.types import Message


class CompactionStrategy(ABC):
    """Abstract base for conversation compaction strategies.

    A compaction strategy reduces a list of messages so that
    the total token count fits within a given *budget*.
    """

    @abstractmethod
    def compact(self, messages: list[Message], budget: int) -> list[Message]:
        """Return a compacted copy of *messages* that fits within *budget* tokens.

        Implementations MUST NOT mutate the original list or its elements.
        """

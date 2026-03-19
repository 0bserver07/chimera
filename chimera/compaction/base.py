from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

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


class CompactionUrgency(str, Enum):
    """How urgently compaction is needed."""

    NONE = "none"
    SOFT = "soft"
    HARD = "hard"


@dataclass
class AtomicGroup:
    """A group of messages that must not be split during compaction.

    Attributes:
        start_index: First message index in the group.
        end_index: Last message index in the group (inclusive).
        group_type: Category (``"tool_call"``, ``"reasoning_chain"``, ``"system"``).
    """

    start_index: int
    end_index: int
    group_type: str

    @property
    def size(self) -> int:
        """Number of messages in this group."""
        return self.end_index - self.start_index + 1


class CompactionView:
    """Context view that tracks atomic groups and safe compaction points.

    Args:
        messages: The conversation messages.
        atomic_groups: Explicit atomic groups. If ``None``, auto-detected.
    """

    def __init__(
        self,
        messages: list[Message],
        atomic_groups: list[AtomicGroup] | None = None,
    ) -> None:
        self.messages = messages
        self.atomic_groups = (
            atomic_groups if atomic_groups is not None else self._detect_groups()
        )
        self._safe_indices: list[int] | None = None

    @property
    def safe_removal_indices(self) -> list[int]:
        """Indices where messages can be safely removed without breaking atomicity."""
        if self._safe_indices is None:
            self._safe_indices = self._compute_safe_indices()
        return self._safe_indices

    @property
    def token_estimate(self) -> int:
        """Rough token count estimate (4 chars per token)."""
        return sum(len(str(m.content)) // 4 for m in self.messages)

    def _detect_groups(self) -> list[AtomicGroup]:
        """Auto-detect atomic groups from message patterns."""
        groups: list[AtomicGroup] = []

        if self.messages and self.messages[0].role == "system":
            groups.append(AtomicGroup(0, 0, "system"))

        i = 0
        while i < len(self.messages):
            msg = self.messages[i]
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                end = i + 1
                while end < len(self.messages):
                    if self.messages[end].role == "tool":
                        end += 1
                    else:
                        break
                if end > i + 1:
                    groups.append(AtomicGroup(i, end - 1, "tool_call"))
                    i = end
                    continue
            i += 1

        return groups

    def _compute_safe_indices(self) -> list[int]:
        """Find message indices not protected by any atomic group."""
        protected: set[int] = set()
        for group in self.atomic_groups:
            for i in range(group.start_index, group.end_index + 1):
                protected.add(i)

        # System message (index 0) always protected
        if self.messages:
            protected.add(0)

        # Last message (current turn) always protected
        if self.messages:
            protected.add(len(self.messages) - 1)

        return [i for i in range(len(self.messages)) if i not in protected]

    def compact(self, indices_to_remove: list[int]) -> CompactionView:
        """Remove messages at given indices, respecting atomicity.

        Args:
            indices_to_remove: Indices of messages to remove.

        Returns:
            A new :class:`CompactionView` with protected messages preserved.
        """
        safe = set(self.safe_removal_indices)
        actual_removals = {i for i in indices_to_remove if i in safe}

        new_messages = [
            m for i, m in enumerate(self.messages) if i not in actual_removals
        ]
        return CompactionView(new_messages)


@dataclass
class CompactionMetadata:
    """Metadata preserved across compaction boundaries."""

    read_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0

    def merge(self, other: CompactionMetadata) -> CompactionMetadata:
        return CompactionMetadata(
            read_files=list(dict.fromkeys(self.read_files + other.read_files)),
            modified_files=list(dict.fromkeys(self.modified_files + other.modified_files)),
            tokens_before=other.tokens_before,
            tokens_after=other.tokens_after,
        )

    def to_prompt_section(self) -> str:
        if not self.read_files and not self.modified_files:
            return ""
        lines = ["## Files you've been working with"]
        if self.modified_files:
            lines.append("Modified: " + ", ".join(self.modified_files))
        if self.read_files:
            lines.append("Read: " + ", ".join(self.read_files))
        return "\n".join(lines)


class FileAwareCompaction(CompactionStrategy):
    """Mixin for compaction strategies that use file tracking metadata."""

    _file_metadata: CompactionMetadata | None = None

    def set_metadata(self, metadata: CompactionMetadata) -> None:
        self._file_metadata = metadata

    def get_file_prompt_section(self) -> str:
        if self._file_metadata is None:
            return ""
        return self._file_metadata.to_prompt_section()

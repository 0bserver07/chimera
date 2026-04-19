"""Content replacement state for persisting large tool results to disk."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContentReplacementEntry:
    """Record of a single tool result that was persisted to disk."""

    tool_use_id: str
    persisted_path: str
    preview: str
    original_size: int
    timestamp: float


@dataclass
class ContentReplacementState:
    """Tracks which tool results have been persisted vs kept inline.

    Once a decision is recorded for a tool_use_id it is frozen:
    subsequent calls to :meth:`should_persist` return the frozen answer.
    """

    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, ContentReplacementEntry] = field(default_factory=dict)
    per_tool_max_chars: int = 30_000
    per_message_max_chars: int = 100_000
    preview_size_bytes: int = 2048

    def should_persist(
        self,
        tool_use_id: str,
        result_size: int,
        tool_max: int | None = None,
    ) -> bool:
        """Decide whether a tool result should be persisted to disk.

        If the *tool_use_id* has already been seen, return the frozen
        decision (True if it was persisted, False if kept inline).
        Otherwise compare *result_size* against the threshold.
        """
        if tool_use_id in self.seen_ids:
            return tool_use_id in self.replacements
        threshold = tool_max if tool_max is not None else self.per_tool_max_chars
        return result_size > threshold

    def record_decision(
        self,
        tool_use_id: str,
        persisted_path: str | None = None,
        preview: str | None = None,
        original_size: int = 0,
    ) -> None:
        """Record a persist/inline decision for *tool_use_id*.

        If *persisted_path* is provided, the result was persisted.
        Otherwise it was kept inline.  Either way the id is added
        to :attr:`seen_ids` so future lookups are frozen.
        """
        self.seen_ids.add(tool_use_id)
        if persisted_path is not None:
            import time

            self.replacements[tool_use_id] = ContentReplacementEntry(
                tool_use_id=tool_use_id,
                persisted_path=persisted_path,
                preview=preview or "",
                original_size=original_size,
                timestamp=time.time(),
            )

    def clone(self) -> ContentReplacementState:
        """Return a deep copy so mutations are independent."""
        return ContentReplacementState(
            seen_ids=copy.copy(self.seen_ids),
            replacements=copy.copy(self.replacements),
            per_tool_max_chars=self.per_tool_max_chars,
            per_message_max_chars=self.per_message_max_chars,
            preview_size_bytes=self.preview_size_bytes,
        )

    def enforce_budget(self, messages: list[Any]) -> list[Any]:
        """Replace persisted tool results with previews in message list."""
        result = []
        for msg in messages:
            tool_use_id = getattr(msg, 'call_id', None) or getattr(msg, 'tool_use_id', None)
            if tool_use_id and tool_use_id in self.replacements:
                entry = self.replacements[tool_use_id]
                # Create a copy with preview content
                import copy
                new_msg = copy.copy(msg)
                if hasattr(new_msg, 'content'):
                    new_msg.content = entry.preview + f"\n[Full output: {entry.persisted_path}]"
                result.append(new_msg)
            else:
                result.append(msg)
        return result

    @classmethod
    def reconstruct_from_transcript(
        cls,
        entries: list[ContentReplacementEntry],
    ) -> ContentReplacementState:
        """Rebuild state from a list of previously-recorded entries."""
        state = cls()
        for entry in entries:
            state.seen_ids.add(entry.tool_use_id)
            state.replacements[entry.tool_use_id] = entry
        return state

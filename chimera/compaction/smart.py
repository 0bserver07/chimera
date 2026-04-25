"""Smart context compaction: preserve recent turns, summarize older ones.

Unlike whole-context compaction, this strategy keeps the last K messages
verbatim while summarizing everything before that into a condensed block.
"""
from __future__ import annotations

from dataclasses import dataclass

from chimera.compaction.base import CompactionStrategy
from chimera.types import Message


@dataclass
class SmartCompactionConfig:
    """Configuration for smart compaction.

    Args:
        preserve_recent: Number of recent messages to keep verbatim.
        summary_prefix: Prefix for the summary message.
        max_summary_chars: Approximate max characters for the summary.
    """

    preserve_recent: int = 10
    summary_prefix: str = "[Conversation summary]"
    max_summary_chars: int = 2000


class SmartCompaction(CompactionStrategy):
    """Preserve recent messages, summarize older ones.

    Example::

        compaction = SmartCompaction(config=SmartCompactionConfig(preserve_recent=5))
        compacted = compaction.compact(messages, budget=4000)
    """

    def __init__(self, config: SmartCompactionConfig | None = None) -> None:
        self._config = config or SmartCompactionConfig()

    def compact(self, messages: list[Message], budget: int) -> list[Message]:
        """Compact messages by summarizing older ones.

        Args:
            messages: Full message list.
            budget: Token budget (used as a hint but primary logic is
                message-count based via preserve_recent).

        Returns:
            Compacted message list with a summary replacing older messages.
        """
        preserve = self._config.preserve_recent

        if len(messages) <= preserve:
            return list(messages)

        # Split: older messages to summarize, recent messages to keep
        older = messages[:-preserve]
        recent = messages[-preserve:]

        # Build summary of older messages
        summary_parts: list[str] = []
        for msg in older:
            role = msg.role
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if len(content) > 200:
                content = content[:200] + "..."
            if msg.tool_calls:
                tool_names = ", ".join(tc.name for tc in msg.tool_calls)
                summary_parts.append(f"[{role}: called {tool_names}]")
            elif role == "tool":
                summary_parts.append(f"[tool result: {content[:100]}]")
            else:
                summary_parts.append(f"[{role}: {content}]")

        summary_text = "\n".join(summary_parts)
        max_chars = self._config.max_summary_chars
        if len(summary_text) > max_chars:
            summary_text = summary_text[:max_chars] + "\n[... earlier messages truncated]"

        summary_msg = Message.system(
            f"{self._config.summary_prefix}\n{summary_text}"
        )

        return [summary_msg] + list(recent)

    @property
    def summarized_count(self) -> int:
        """Number of messages that were summarized in the last compact call."""
        return getattr(self, "_last_summarized", 0)

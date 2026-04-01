"""Conversation compaction utilities — snip old tool results and truncate.

:class:`SnipCompactor` removes old tool-role messages from the conversation
history, keeping only the most recent *N*.  :class:`MicroCompactor` truncates
individual messages that exceed a character threshold.
"""

from __future__ import annotations

import copy

from chimera.types import Message


class SnipCompactor:
    """Remove old tool results from conversation, keeping only summaries.

    Args:
        max_tool_results_to_keep: Number of most-recent tool messages to
            preserve.  Older ones are dropped.
    """

    def __init__(self, max_tool_results_to_keep: int = 10) -> None:
        self._max_keep = max_tool_results_to_keep

    def snip_if_needed(self, messages: list[Message]) -> tuple[list[Message], bool]:
        """Remove old tool results, keep only recent ones.

        Returns:
            A tuple of ``(messages, snipped)`` where *snipped* is ``True``
            if any messages were removed.
        """
        tool_results = [(i, m) for i, m in enumerate(messages) if getattr(m, "role", "") == "tool"]

        if len(tool_results) <= self._max_keep:
            return messages, False

        # Keep the last N tool results, remove earlier ones
        to_remove = {i for i, _ in tool_results[: -self._max_keep]}
        result = [m for i, m in enumerate(messages) if i not in to_remove]
        return result, True


class MicroCompactor:
    """Trim whitespace and truncate very long individual messages.

    Args:
        max_message_chars: Character limit per message.  Messages exceeding
            this are split so that the first and last *half* of the limit are
            kept, with a marker in between.
    """

    def __init__(self, max_message_chars: int = 50_000) -> None:
        self._max_chars = max_message_chars

    def compact(self, messages: list[Message]) -> tuple[list[Message], bool]:
        """Trim oversized messages.

        Returns:
            A tuple of ``(messages, compacted)`` where *compacted* is
            ``True`` if any message was truncated.
        """
        compacted = False
        result: list[Message] = []
        for msg in messages:
            content = getattr(msg, "content", "")
            if len(content) > self._max_chars:
                new_msg = copy.copy(msg)
                half = self._max_chars // 2
                new_msg.content = (
                    content[:half]
                    + f"\n... [{len(content) - self._max_chars} chars removed] ...\n"
                    + content[-half:]
                )
                result.append(new_msg)
                compacted = True
            else:
                result.append(msg)
        return result, compacted

"""Thought stripping: remove extended thinking blocks from older messages.

When using models with extended thinking, the thinking blocks consume context
but have diminishing value after the turn. Strip thinking content from older
messages, keeping only the final output. Can reclaim 30-50% of context.
"""
from __future__ import annotations

from chimera.compaction.base import CompactionStrategy
from chimera.types import Message


class ThoughtStripCompaction(CompactionStrategy):
    """Strip thinking blocks from older messages.

    Args:
        preserve_recent: Keep thinking in the last N assistant messages.

    Example::

        compaction = ThoughtStripCompaction(preserve_recent=2)
        compacted = compaction.compact(messages, budget=8000)
    """

    def __init__(self, preserve_recent: int = 2) -> None:
        self._preserve_recent = preserve_recent

    def compact(self, messages: list[Message], budget: int) -> list[Message]:
        """Strip thinking blocks from older assistant messages.

        Args:
            messages: Full message list.
            budget: Token budget (not directly used, but part of the interface).

        Returns:
            Messages with thinking blocks removed from older turns.
        """
        # Find assistant message indices
        assistant_indices = [
            i for i, m in enumerate(messages) if m.role == "assistant"
        ]

        if len(assistant_indices) <= self._preserve_recent:
            return list(messages)

        # Indices to strip (all except the most recent N)
        strip_indices = set(assistant_indices[: -self._preserve_recent])

        result: list[Message] = []
        for i, msg in enumerate(messages):
            if i in strip_indices:
                result.append(_strip_thinking(msg))
            else:
                result.append(msg)

        return result


def _strip_thinking(msg: Message) -> Message:
    """Remove thinking content from an assistant message."""
    content = msg.content
    if not isinstance(content, str):
        return msg

    # Pattern 1: <thinking>...</thinking> blocks
    import re
    stripped = re.sub(
        r"<thinking>.*?</thinking>",
        "",
        content,
        flags=re.DOTALL,
    )

    # Pattern 2: [thinking]...[/thinking] blocks
    stripped = re.sub(
        r"\[thinking\].*?\[/thinking\]",
        "",
        stripped,
        flags=re.DOTALL,
    )

    stripped = stripped.strip()
    if stripped == content:
        return msg  # No change

    # Check metadata for thinking_tokens
    new_msg = Message(
        role=msg.role,
        content=stripped,
        tool_calls=msg.tool_calls,
        call_id=msg.call_id,
    )
    return new_msg


def estimate_thinking_tokens(messages: list[Message]) -> int:
    """Estimate how many tokens are used by thinking blocks."""
    import re
    total = 0
    for msg in messages:
        if msg.role != "assistant" or not isinstance(msg.content, str):
            continue
        for pattern in [r"<thinking>.*?</thinking>", r"\[thinking\].*?\[/thinking\]"]:
            for m in re.finditer(pattern, msg.content, re.DOTALL):
                total += len(m.group()) // 4  # rough token estimate
    return total

# chimera/core/compression.py
from __future__ import annotations

from chimera.types import Message


class ContextCompressor:
    """Compresses conversation history to fit within context limits.

    Strategy: Keep first N and last M messages, replace middle with a summary.
    """

    def __init__(
        self,
        max_messages: int = 50,
        keep_first: int = 2,
        keep_last: int = 10,
    ) -> None:
        self.max_messages = max_messages
        self.keep_first = keep_first
        self.keep_last = keep_last

    def compress(self, messages: list[Message]) -> list[Message]:
        """Compress messages if they exceed max_messages."""
        if len(messages) <= self.max_messages:
            return list(messages)

        first = messages[:self.keep_first]
        last = messages[-self.keep_last:]
        middle = messages[self.keep_first:-self.keep_last] if self.keep_last > 0 else messages[self.keep_first:]

        # Summarize the middle section
        summary_text = self._summarize(middle)
        summary = Message.system(f"[Compressed {len(middle)} messages] {summary_text}")

        return first + [summary] + last

    def _summarize(self, messages: list[Message]) -> str:
        """Create a brief summary of compressed messages."""
        tool_calls = 0
        user_msgs = 0
        assistant_msgs = 0
        for m in messages:
            if m.role == "user":
                user_msgs += 1
            elif m.role == "assistant":
                assistant_msgs += 1
                tool_calls += len(m.tool_calls)
            elif m.role == "tool":
                tool_calls += 1

        parts = []
        if user_msgs:
            parts.append(f"{user_msgs} user messages")
        if assistant_msgs:
            parts.append(f"{assistant_msgs} assistant messages")
        if tool_calls:
            parts.append(f"{tool_calls} tool interactions")

        return f"Summarized: {', '.join(parts)}" if parts else "Summarized conversation."

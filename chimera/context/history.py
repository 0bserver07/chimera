from __future__ import annotations

from abc import ABC, abstractmethod

from chimera.types import Message


class HistoryProcessor(ABC):
    """Process conversation history before sending to LLM."""

    @abstractmethod
    def process(self, messages: list[Message]) -> list[Message]:
        """Transform messages. Returns a new list."""


class TruncateProcessor(HistoryProcessor):
    """Keep only the last N messages."""

    def __init__(self, max_messages: int = 20) -> None:
        self._max = max_messages

    def process(self, messages: list[Message]) -> list[Message]:
        if len(messages) <= self._max:
            return list(messages)
        return list(messages[-self._max:])


class PruneProcessor(HistoryProcessor):
    """Remove tool result content from old messages, keeping structure.

    Keeps the last N tool results intact, prunes older ones to just
    their role/summary. This saves tokens while preserving conversation flow.
    """

    def __init__(self, keep_last_n_results: int = 3) -> None:
        self._keep_last = keep_last_n_results

    def process(self, messages: list[Message]) -> list[Message]:
        result = []
        # Find tool result messages
        tool_indices = [i for i, m in enumerate(messages) if m.role == "tool"]
        prune_before = tool_indices[-self._keep_last] if len(tool_indices) > self._keep_last else -1

        for i, msg in enumerate(messages):
            if msg.role == "tool" and i < prune_before:
                # Prune: replace content with summary but preserve the
                # tool call id — without it the message is invalid for
                # Anthropic/OpenAI (which require tool_call_id on tool
                # role messages to match a prior assistant tool_call).
                result.append(
                    Message(
                        role="tool",
                        content="[pruned]",
                        call_id=msg.call_id,
                    )
                )
            else:
                result.append(msg)
        return result


class CompressProcessor(HistoryProcessor):
    """Compress old messages into a summary, keeping recent ones intact.

    In LLM mode, uses a provider to generate the summary.
    In simple mode, just concatenates and truncates.
    """

    def __init__(self, keep_recent: int = 5, max_summary_tokens: int = 500) -> None:
        self._keep_recent = keep_recent
        self._max_summary = max_summary_tokens

    def process(self, messages: list[Message]) -> list[Message]:
        if len(messages) <= self._keep_recent:
            return list(messages)

        old = messages[:-self._keep_recent]
        recent = messages[-self._keep_recent:]

        # Simple compression: concatenate old messages into a summary
        summary_parts = []
        for m in old:
            prefix = m.role.upper()[:4]
            content = m.content[:200] if m.content else ""
            summary_parts.append(f"[{prefix}] {content}")

        summary_text = "[Conversation summary]\n" + "\n".join(summary_parts)
        # Truncate to budget
        max_chars = self._max_summary * 4
        if len(summary_text) > max_chars:
            summary_text = summary_text[:max_chars] + "\n[...truncated]"

        summary_msg = Message(role="user", content=summary_text)
        return [summary_msg] + list(recent)


class CompositeProcessor(HistoryProcessor):
    """Chain multiple processors in sequence."""

    def __init__(self, processors: list[HistoryProcessor]) -> None:
        self._processors = processors

    def process(self, messages: list[Message]) -> list[Message]:
        result = list(messages)
        for proc in self._processors:
            result = proc.process(result)
        return result

from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.types import Message

from chimera.compaction.base import FileAwareCompaction

if TYPE_CHECKING:
    from chimera.providers.base import Provider


class SummaryCompaction(FileAwareCompaction):
    """Replace the middle portion of a conversation with a summary.

    When a *provider* is supplied the summary is generated via an LLM call;
    otherwise a simple textual count of messages by role is produced.
    """

    def __init__(
        self,
        provider: Provider | None = None,
        keep_first: int = 2,
        keep_last: int = 10,
        summary_max_tokens: int = 500,
    ) -> None:
        self._provider = provider
        self.keep_first = keep_first
        self.keep_last = keep_last
        self.summary_max_tokens = summary_max_tokens

    def compact(self, messages: list[Message], budget: int) -> list[Message]:
        """Return messages with the middle section replaced by a summary."""
        total = len(messages)
        min_kept = self.keep_first + self.keep_last
        if total <= min_kept:
            return list(messages)

        first = messages[: self.keep_first]
        last = messages[-self.keep_last :] if self.keep_last > 0 else []
        middle = (
            messages[self.keep_first : -self.keep_last]
            if self.keep_last > 0
            else messages[self.keep_first :]
        )

        summary_text = self._summarize(middle)
        summary_msg = Message.system(
            f"[Compacted {len(middle)} messages]\n{summary_text}"
        )
        return first + [summary_msg] + last

    # ------------------------------------------------------------------
    # Summarisation back-ends
    # ------------------------------------------------------------------

    def _summarize(self, messages: list[Message]) -> str:
        if self._provider is not None:
            return self._summarize_with_provider(messages)
        return self._summarize_simple(messages)

    def _summarize_simple(self, messages: list[Message]) -> str:
        """Produce a human-readable count of messages by role."""
        counts: dict[str, int] = {}
        tool_calls = 0
        for msg in messages:
            counts[msg.role] = counts.get(msg.role, 0) + 1
            tool_calls += len(msg.tool_calls)

        parts: list[str] = []
        for role in ("user", "assistant", "system", "tool"):
            n = counts.get(role, 0)
            if n:
                parts.append(f"{n} {role} message{'s' if n != 1 else ''}")
        if tool_calls:
            parts.append(f"{tool_calls} tool call{'s' if tool_calls != 1 else ''}")

        summary = f"Summarized: {', '.join(parts)}." if parts else "Summarized conversation."
        file_section = self.get_file_prompt_section()
        if file_section:
            summary += "\n\n" + file_section
        return summary

    def _summarize_with_provider(self, messages: list[Message]) -> str:
        """Use the configured LLM provider to produce a summary."""
        conversation = "\n".join(
            f"[{m.role}] {m.content[:200]}" for m in messages
        )
        file_section = self.get_file_prompt_section()
        extra = f"\n\nFiles tracked:\n{file_section}" if file_section else ""
        prompt = (
            "Summarize the following conversation excerpt in a concise paragraph. "
            "Focus on key decisions, actions taken, and results.\n\n"
            f"{conversation}{extra}"
        )
        response = self._provider.complete(  # type: ignore[union-attr]
            messages=[Message.user(prompt)],
            max_tokens=self.summary_max_tokens,
        )
        return response.content

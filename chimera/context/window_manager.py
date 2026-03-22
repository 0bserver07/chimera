"""Unified context window management.

Combines :class:`~chimera.compaction.smart.SmartCompaction`,
:class:`~chimera.compaction.thought_strip.ThoughtStripCompaction`,
:class:`~chimera.context.focus.FocusChain`, and
:class:`~chimera.context.consolidation.MemoryConsolidator` into a single
manager that monitors context usage and takes graduated action:

- **70 %** — start being selective (:class:`FocusChain`)
- **85 %** — summarize older turns (:class:`SmartCompaction`) + strip thinking
- **90 %** — consolidate facts + aggressive compact

Example::

    manager = ContextWindowManager(max_tokens=128_000)
    managed = manager.check(messages)
    # or, for full consolidation:
    managed, memory = manager.consolidate(messages)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from chimera.compaction.smart import SmartCompaction, SmartCompactionConfig
from chimera.compaction.thought_strip import ThoughtStripCompaction
from chimera.context.consolidation import ConsolidatedMemory, MemoryConsolidator
from chimera.context.focus import FocusChain
from chimera.types import Message


class WindowUrgency(str, Enum):
    """How urgently the context window needs management."""

    NONE = "none"
    SELECTIVE = "selective"      # 70% — start being selective
    SUMMARIZE = "summarize"     # 85% — summarize + strip thinking
    AGGRESSIVE = "aggressive"   # 90% — consolidate + hard compact


@dataclass
class WindowState:
    """Snapshot of context window usage after a check.

    Attributes:
        tokens_before: Estimated token count before management.
        tokens_after: Estimated token count after management.
        urgency: The urgency level that was detected.
        actions_taken: List of actions applied (e.g. ``"focus_chain"``,
            ``"smart_compaction"``, ``"thought_strip"``, ``"consolidation"``).
        messages_removed: Number of messages removed or summarized.
    """

    tokens_before: int = 0
    tokens_after: int = 0
    urgency: WindowUrgency = WindowUrgency.NONE
    actions_taken: list[str] = field(default_factory=list)
    messages_removed: int = 0


def _estimate_tokens(messages: list[Message]) -> int:
    """Rough token count: 4 chars per token."""
    return sum(len(str(m.content)) // 4 for m in messages)


class ContextWindowManager:
    """Proactive context window management.

    Monitors context usage and takes graduated action at configurable
    thresholds.

    Args:
        max_tokens: Maximum context window size in tokens.
        selective_threshold: Usage ratio to trigger selective filtering
            (default ``0.70``).
        summarize_threshold: Usage ratio to trigger summarization
            (default ``0.85``).
        aggressive_threshold: Usage ratio to trigger aggressive compaction
            (default ``0.90``).
        preserve_recent: Number of recent messages to keep verbatim during
            summarization (passed to :class:`SmartCompaction`).
        focus_budget: Token budget for the :class:`FocusChain` stage
            (default ``4000``).
    """

    def __init__(
        self,
        max_tokens: int = 128_000,
        selective_threshold: float = 0.70,
        summarize_threshold: float = 0.85,
        aggressive_threshold: float = 0.90,
        preserve_recent: int = 10,
        focus_budget: int = 4000,
    ) -> None:
        if not (0 < selective_threshold < summarize_threshold < aggressive_threshold <= 1.0):
            raise ValueError(
                "Thresholds must satisfy 0 < selective < summarize < aggressive <= 1.0"
            )

        self.max_tokens = max_tokens
        self.selective_threshold = selective_threshold
        self.summarize_threshold = summarize_threshold
        self.aggressive_threshold = aggressive_threshold

        self._smart = SmartCompaction(
            SmartCompactionConfig(preserve_recent=preserve_recent)
        )
        self._thought_strip = ThoughtStripCompaction(preserve_recent=2)
        self._focus = FocusChain(token_budget=focus_budget)
        self._consolidator = MemoryConsolidator()
        self._last_state: WindowState | None = None

    # ── Public API ────────────────────────────────────────────────────

    @property
    def last_state(self) -> WindowState | None:
        """The :class:`WindowState` from the most recent :meth:`check` call."""
        return self._last_state

    def urgency(self, messages: list[Message]) -> WindowUrgency:
        """Determine the urgency level for the given messages.

        Args:
            messages: Current conversation messages.

        Returns:
            The urgency level based on token usage vs thresholds.
        """
        if self.max_tokens == 0:
            return WindowUrgency.NONE
        tokens = _estimate_tokens(messages)
        ratio = tokens / self.max_tokens
        if ratio >= self.aggressive_threshold:
            return WindowUrgency.AGGRESSIVE
        if ratio >= self.summarize_threshold:
            return WindowUrgency.SUMMARIZE
        if ratio >= self.selective_threshold:
            return WindowUrgency.SELECTIVE
        return WindowUrgency.NONE

    def check(self, messages: list[Message]) -> list[Message]:
        """Check context usage and apply the appropriate management strategy.

        This is the primary entry point.  Call it before each LLM invocation
        to keep the context within budget.

        Args:
            messages: Current conversation messages.

        Returns:
            A (possibly compacted) list of messages that fits the budget.
        """
        tokens_before = _estimate_tokens(messages)
        level = self.urgency(messages)
        actions: list[str] = []
        result = list(messages)

        if level == WindowUrgency.NONE:
            self._last_state = WindowState(
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                urgency=level,
            )
            return result

        if level.value in ("selective", "summarize", "aggressive"):
            result = self._apply_focus(result)
            actions.append("focus_chain")

        if level.value in ("summarize", "aggressive"):
            result = self._thought_strip.compact(result, budget=self.max_tokens)
            actions.append("thought_strip")
            budget = int(self.max_tokens * self.selective_threshold)
            result = self._smart.compact(result, budget=budget)
            actions.append("smart_compaction")

        if level == WindowUrgency.AGGRESSIVE:
            result = self._aggressive_compact(result)
            actions.append("aggressive_compact")

        tokens_after = _estimate_tokens(result)
        self._last_state = WindowState(
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            urgency=level,
            actions_taken=actions,
            messages_removed=len(messages) - len(result),
        )
        return result

    def consolidate(
        self, messages: list[Message],
    ) -> tuple[list[Message], ConsolidatedMemory]:
        """Full consolidation: compact messages **and** extract a memory snapshot.

        Runs :meth:`check` for compaction, then uses
        :class:`MemoryConsolidator` to extract structured facts from the
        *original* (pre-compaction) messages.

        Args:
            messages: Current conversation messages.

        Returns:
            A tuple of ``(compacted_messages, consolidated_memory)``.
        """
        # Extract facts from original messages before compaction
        self._consolidator.clear()
        self._consolidator.extract_from_messages(messages, source="conversation")

        # Compact
        compacted = self.check(messages)

        # Consolidate facts
        memory = self._consolidator.consolidate()

        # If we have a useful summary, inject it as a system message
        if memory.summary and self._last_state and self._last_state.urgency != WindowUrgency.NONE:
            summary_msg = Message.system(
                f"[Consolidated memory]\n{memory.summary}"
            )
            # Insert after the first system message (if any) or at index 0
            insert_idx = 0
            if compacted and compacted[0].role == "system":
                insert_idx = 1
            compacted.insert(insert_idx, summary_msg)

        return compacted, memory

    # ── Private helpers ───────────────────────────────────────────────

    def _apply_focus(self, messages: list[Message]) -> list[Message]:
        """Apply FocusChain to rank and filter messages by relevance.

        Older non-system, non-tool messages with short content get lower
        relevance.  Recent messages always get high relevance.

        Args:
            messages: Messages to filter.

        Returns:
            Filtered messages (may be unchanged if everything fits).
        """
        if len(messages) <= 4:
            return messages

        # Always keep system messages and the last few messages
        keep_last = min(6, len(messages))

        self._focus.clear()
        head: list[Message] = []
        tail: list[Message] = messages[-keep_last:]
        middle: list[tuple[int, Message]] = []

        for i, msg in enumerate(messages[:-keep_last]):
            if msg.role == "system":
                head.append(msg)
            else:
                middle.append((i, msg))

        if not middle:
            return messages

        # Score middle messages: newer = higher relevance, tool results = higher relevance
        total_middle = len(middle)
        for rank, (idx, msg) in enumerate(middle):
            # Recency score: 0.2 (oldest) → 0.8 (newest)
            recency = 0.2 + 0.6 * (rank / max(1, total_middle - 1))
            # Tool messages get a boost
            type_boost = 0.1 if msg.role == "tool" else 0.0
            relevance = min(1.0, recency + type_boost)
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            self._focus.add(content, source=f"msg:{idx}", relevance=relevance)

        selected = self._focus.select()
        selected_sources = {item.source for item in selected}

        filtered_middle = [
            msg for idx, msg in middle
            if f"msg:{idx}" in selected_sources
        ]

        return head + filtered_middle + tail

    def _aggressive_compact(self, messages: list[Message]) -> list[Message]:
        """Emergency compaction: keep system prompt + last N messages.

        Args:
            messages: Already partially compacted messages.

        Returns:
            Aggressively compacted messages.
        """
        keep_last = 5
        result: list[Message] = []

        # Keep system messages at the start
        for msg in messages:
            if msg.role == "system":
                result.append(msg)
            else:
                break

        # Add a compaction notice
        result.append(Message.system(
            "[Previous context was aggressively compacted due to length. "
            "Recent conversation follows.]"
        ))

        # Keep the last N messages
        tail = [m for m in messages if m.role != "system"][-keep_last:]
        result.extend(tail)

        return result

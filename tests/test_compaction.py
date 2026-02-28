from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest

from chimera.compaction import (
    CompactionStrategy,
    CompositeCompaction,
    PruneCompaction,
    SummaryCompaction,
    TokenCounter,
)
from chimera.compaction.counter import _HAS_TIKTOKEN
from chimera.types import Message, ToolCall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_messages(n: int) -> list[Message]:
    """Create *n* simple user messages."""
    return [Message.user(f"message {i}") for i in range(n)]


def _long_tool_message(lines: int = 100) -> Message:
    """Return a tool message whose content has the given number of lines."""
    content = "\n".join(f"line {i}" for i in range(lines))
    return Message.tool("call_1", content)


# =====================================================================
# TokenCounter
# =====================================================================


class TestTokenCounter:
    def test_count_empty_string(self) -> None:
        counter = TokenCounter()
        assert counter.count("") == 0

    def test_count_returns_positive_for_text(self) -> None:
        counter = TokenCounter()
        result = counter.count("hello world, this is a test string")
        assert result > 0

    def test_count_heuristic_without_tiktoken(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Forcibly disable tiktoken encoding to exercise the fallback."""
        counter = TokenCounter()
        counter._encoding = None  # type: ignore[assignment]
        text = "a" * 100
        assert counter.count(text) == 25  # 100 / 4

    def test_count_messages_sums_content(self) -> None:
        counter = TokenCounter()
        msgs = [Message.user("hello"), Message.assistant("world")]
        total = counter.count_messages(msgs)
        assert total == counter.count("hello") + counter.count("world")

    def test_count_messages_includes_tool_call_args(self) -> None:
        counter = TokenCounter()
        tc = ToolCall(id="t1", name="read", arguments={"path": "/foo/bar"})
        msgs = [Message.assistant("ok", tool_calls=[tc])]
        total = counter.count_messages(msgs)
        # Must be more than just the content tokens
        assert total > counter.count("ok")

    def test_count_messages_empty_list(self) -> None:
        counter = TokenCounter()
        assert counter.count_messages([]) == 0

    @pytest.mark.skipif(not _HAS_TIKTOKEN, reason="tiktoken not installed")
    def test_count_with_tiktoken(self) -> None:
        counter = TokenCounter(model="cl100k_base")
        assert counter._encoding is not None
        result = counter.count("hello world")
        assert isinstance(result, int)
        assert result > 0


# =====================================================================
# PruneCompaction
# =====================================================================


class TestPruneCompaction:
    def test_short_messages_pass_through(self) -> None:
        pruner = PruneCompaction(max_tool_output_lines=50)
        msgs = [Message.user("hi"), Message.assistant("hello")]
        result = pruner.compact(msgs, budget=9999)
        assert len(result) == 2
        assert result[0].content == "hi"
        assert result[1].content == "hello"

    def test_short_tool_output_untouched(self) -> None:
        pruner = PruneCompaction(max_tool_output_lines=50)
        tool_msg = Message.tool("c1", "short output\nonly two lines")
        result = pruner.compact([tool_msg], budget=9999)
        assert result[0].content == tool_msg.content

    def test_long_tool_output_truncated(self) -> None:
        pruner = PruneCompaction(max_tool_output_lines=50)
        tool_msg = _long_tool_message(lines=100)
        result = pruner.compact([tool_msg], budget=9999)
        assert len(result) == 1
        assert "... [truncated] ..." in result[0].content
        # Head and tail preserved
        assert "line 0" in result[0].content
        assert "line 99" in result[0].content
        # Something from the middle should be gone
        assert "line 50" not in result[0].content

    def test_preserves_head_and_tail_lines(self) -> None:
        pruner = PruneCompaction(max_tool_output_lines=50)
        tool_msg = _long_tool_message(lines=200)
        result = pruner.compact([tool_msg], budget=9999)
        content = result[0].content
        lines_before_trunc = content.split("... [truncated] ...")[0].strip().splitlines()
        lines_after_trunc = content.split("... [truncated] ...")[1].strip().splitlines()
        assert len(lines_before_trunc) == 20
        assert len(lines_after_trunc) == 20

    def test_non_tool_messages_never_truncated(self) -> None:
        pruner = PruneCompaction(max_tool_output_lines=5)
        long_user = Message.user("\n".join(f"line {i}" for i in range(100)))
        result = pruner.compact([long_user], budget=9999)
        assert result[0].content == long_user.content

    def test_original_messages_not_mutated(self) -> None:
        pruner = PruneCompaction(max_tool_output_lines=50)
        tool_msg = _long_tool_message(lines=100)
        original_content = tool_msg.content
        pruner.compact([tool_msg], budget=9999)
        assert tool_msg.content == original_content


# =====================================================================
# SummaryCompaction
# =====================================================================


class TestSummaryCompactionNoProvider:
    def test_short_conversation_unchanged(self) -> None:
        sc = SummaryCompaction(keep_first=2, keep_last=2)
        msgs = _make_messages(4)
        result = sc.compact(msgs, budget=9999)
        assert len(result) == 4

    def test_text_summary_fallback(self) -> None:
        sc = SummaryCompaction(provider=None, keep_first=1, keep_last=1)
        msgs = _make_messages(10)
        result = sc.compact(msgs, budget=9999)
        # 1 first + 1 summary + 1 last = 3
        assert len(result) == 3
        assert result[0].content == "message 0"
        assert result[-1].content == "message 9"
        summary = result[1]
        assert summary.role == "system"
        assert "Compacted 8 messages" in summary.content
        assert "user message" in summary.content.lower()

    def test_text_summary_counts_roles(self) -> None:
        sc = SummaryCompaction(provider=None, keep_first=0, keep_last=0)
        msgs = [
            Message.user("u1"),
            Message.assistant("a1"),
            Message.user("u2"),
            Message.assistant("a2", tool_calls=[
                ToolCall(id="t1", name="run", arguments={}),
            ]),
            Message.tool("t1", "result"),
        ]
        result = sc.compact(msgs, budget=9999)
        assert len(result) == 1
        summary_text = result[0].content
        assert "2 user messages" in summary_text
        assert "2 assistant messages" in summary_text
        assert "1 tool message" in summary_text
        assert "1 tool call" in summary_text


class TestSummaryCompactionWithProvider:
    def test_provider_called_for_summary(self) -> None:
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "The user asked for file edits and the agent performed them."
        mock_provider.complete.return_value = mock_response

        sc = SummaryCompaction(
            provider=mock_provider,
            keep_first=1,
            keep_last=1,
            summary_max_tokens=300,
        )
        msgs = _make_messages(10)
        result = sc.compact(msgs, budget=9999)

        assert len(result) == 3
        assert "file edits" in result[1].content
        mock_provider.complete.assert_called_once()

        # Verify the call used the right max_tokens
        call_kwargs = mock_provider.complete.call_args
        assert call_kwargs.kwargs.get("max_tokens") == 300

    def test_provider_receives_conversation_excerpt(self) -> None:
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Summary"
        mock_provider.complete.return_value = mock_response

        sc = SummaryCompaction(provider=mock_provider, keep_first=1, keep_last=1)
        msgs = [
            Message.user("first"),
            Message.assistant("second"),
            Message.user("third"),
            Message.user("last"),
        ]
        sc.compact(msgs, budget=9999)

        call_args = mock_provider.complete.call_args
        prompt_msgs = call_args.kwargs.get("messages") or call_args[0][0]
        prompt_text = prompt_msgs[0].content
        # The middle messages should appear in the summarisation prompt
        assert "second" in prompt_text
        assert "third" in prompt_text


# =====================================================================
# CompositeCompaction
# =====================================================================


class TestCompositeCompaction:
    def test_chains_strategies_in_order(self) -> None:
        """Each strategy should be called on the output of the previous one."""
        call_log: list[str] = []

        class StrategyA(CompactionStrategy):
            def compact(self, messages: list[Message], budget: int) -> list[Message]:
                call_log.append("A")
                return messages + [Message.system("from A")]

        class StrategyB(CompactionStrategy):
            def compact(self, messages: list[Message], budget: int) -> list[Message]:
                call_log.append("B")
                return messages + [Message.system("from B")]

        # Start with messages that exceed a tiny budget so both strategies run
        msgs = [Message.user("x" * 400)]  # ~100 heuristic tokens, over budget=0
        comp = CompositeCompaction(strategies=[StrategyA(), StrategyB()])
        result = comp.compact(msgs, budget=0)
        assert call_log == ["A", "B"]
        assert result[-2].content == "from A"
        assert result[-1].content == "from B"

    def test_stops_early_when_under_budget(self) -> None:
        call_log: list[str] = []

        class CheapStrategy(CompactionStrategy):
            """Removes all messages so token count drops to zero."""
            def compact(self, messages: list[Message], budget: int) -> list[Message]:
                call_log.append("cheap")
                return []

        class NeverCalled(CompactionStrategy):
            def compact(self, messages: list[Message], budget: int) -> list[Message]:
                call_log.append("never")
                return messages

        comp = CompositeCompaction(strategies=[CheapStrategy(), NeverCalled()])
        msgs = _make_messages(5)
        result = comp.compact(msgs, budget=999_999)
        # Since the original messages already fit, CheapStrategy should
        # never even be called.
        assert call_log == []
        assert len(result) == 5

    def test_second_strategy_skipped_after_budget_met(self) -> None:
        call_log: list[str] = []

        class HalveStrategy(CompactionStrategy):
            def compact(self, messages: list[Message], budget: int) -> list[Message]:
                call_log.append("halve")
                return messages[: len(messages) // 2]

        class Extra(CompactionStrategy):
            def compact(self, messages: list[Message], budget: int) -> list[Message]:
                call_log.append("extra")
                return messages

        # Use enough tokens so the full set exceeds budget but the halved set fits.
        # Each message contributes some tokens; we measure to set budget correctly.
        counter = TokenCounter()
        msgs = [Message.user(f"word{i} " * 80) for i in range(20)]
        full_count = counter.count_messages(msgs)
        half_msgs = msgs[: len(msgs) // 2]
        half_count = counter.count_messages(half_msgs)
        # Budget sits between half_count and full_count
        budget = (half_count + full_count) // 2
        comp = CompositeCompaction(strategies=[HalveStrategy(), Extra()])
        result = comp.compact(msgs, budget=budget)
        # Only HalveStrategy should have been called
        assert call_log == ["halve"]
        assert len(result) == 10

    def test_empty_strategies_list(self) -> None:
        comp = CompositeCompaction(strategies=[])
        msgs = _make_messages(3)
        result = comp.compact(msgs, budget=9999)
        assert len(result) == 3


# =====================================================================
# Immutability / safety
# =====================================================================


class TestImmutability:
    def test_prune_does_not_mutate_originals(self) -> None:
        pruner = PruneCompaction(max_tool_output_lines=10)
        original = _long_tool_message(lines=100)
        snapshot = copy.deepcopy(original)
        pruner.compact([original], budget=9999)
        assert original == snapshot

    def test_summary_does_not_mutate_originals(self) -> None:
        sc = SummaryCompaction(provider=None, keep_first=1, keep_last=1)
        originals = _make_messages(10)
        snapshot = copy.deepcopy(originals)
        sc.compact(originals, budget=9999)
        assert originals == snapshot

    def test_composite_does_not_mutate_originals(self) -> None:
        comp = CompositeCompaction(strategies=[
            PruneCompaction(max_tool_output_lines=10),
            SummaryCompaction(provider=None, keep_first=1, keep_last=1),
        ])
        originals = [
            Message.user("start"),
            _long_tool_message(lines=100),
            *_make_messages(10),
            Message.user("end"),
        ]
        snapshot = copy.deepcopy(originals)
        comp.compact(originals, budget=50)
        assert originals == snapshot

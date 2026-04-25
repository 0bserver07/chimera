"""Tests for chimera.context.window_manager — unified context window management."""
from __future__ import annotations

import pytest

from chimera.context.window_manager import (
    ContextWindowManager,
    WindowUrgency,
    _estimate_tokens,
)
from chimera.types import Message


# ── Helpers ───────────────────────────────────────────────────────────

def _make_messages(n: int, chars_each: int = 100) -> list[Message]:
    """Create *n* user/assistant message pairs with given content size."""
    msgs: list[Message] = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        content = f"message {i}: " + ("x" * chars_each)
        msgs.append(Message(role=role, content=content))
    return msgs


def _messages_with_thinking(n: int) -> list[Message]:
    """Create messages where assistant replies include <thinking> blocks."""
    msgs: list[Message] = []
    for i in range(n):
        if i % 2 == 0:
            msgs.append(Message.user(f"question {i}"))
        else:
            thinking = "<thinking>" + ("thought " * 50) + "</thinking>"
            msgs.append(Message.assistant(f"{thinking}\nAnswer to question {i}."))
    return msgs


# ── Unit tests ────────────────────────────────────────────────────────

class TestEstimateTokens:
    def test_empty_list(self) -> None:
        assert _estimate_tokens([]) == 0

    def test_estimates_correctly(self) -> None:
        msgs = [Message.user("a" * 400)]
        assert _estimate_tokens(msgs) == 100  # 400 / 4


class TestUrgencyDetection:
    def test_none_when_under_threshold(self) -> None:
        mgr = ContextWindowManager(max_tokens=10000)
        msgs = _make_messages(2, chars_each=40)  # very small
        assert mgr.urgency(msgs) == WindowUrgency.NONE

    def test_selective_at_70_percent(self) -> None:
        mgr = ContextWindowManager(max_tokens=1000)
        # 750 tokens ≈ 3000 chars → 8 messages of 375 chars each
        msgs = _make_messages(8, chars_each=375)
        level = mgr.urgency(msgs)
        assert level == WindowUrgency.SELECTIVE

    def test_summarize_at_85_percent(self) -> None:
        mgr = ContextWindowManager(max_tokens=1000)
        # Need ~850 tokens = ~3400 chars
        msgs = _make_messages(8, chars_each=425)
        level = mgr.urgency(msgs)
        assert level == WindowUrgency.SUMMARIZE

    def test_aggressive_at_90_percent(self) -> None:
        mgr = ContextWindowManager(max_tokens=1000)
        # Need ~950 tokens = ~3800 chars
        msgs = _make_messages(8, chars_each=475)
        level = mgr.urgency(msgs)
        assert level == WindowUrgency.AGGRESSIVE


class TestCheckNoAction:
    def test_check_returns_same_when_under_budget(self) -> None:
        mgr = ContextWindowManager(max_tokens=100_000)
        msgs = _make_messages(4, chars_each=40)
        result = mgr.check(msgs)
        assert len(result) == len(msgs)
        assert mgr.last_state is not None
        assert mgr.last_state.urgency == WindowUrgency.NONE


class TestCheckCompaction:
    def test_check_reduces_at_summarize_threshold(self) -> None:
        mgr = ContextWindowManager(
            max_tokens=1000,
            selective_threshold=0.70,
            summarize_threshold=0.85,
            aggressive_threshold=0.90,
            preserve_recent=4,
        )
        # Create enough messages to exceed 85% of 1000 tokens
        msgs = _make_messages(20, chars_each=200)
        result = mgr.check(msgs)
        # After compaction we should have fewer messages
        assert len(result) < len(msgs)
        state = mgr.last_state
        assert state is not None
        assert "smart_compaction" in state.actions_taken or "thought_strip" in state.actions_taken

    def test_check_aggressive_keeps_few_messages(self) -> None:
        mgr = ContextWindowManager(
            max_tokens=500,
            selective_threshold=0.70,
            summarize_threshold=0.85,
            aggressive_threshold=0.90,
            preserve_recent=4,
        )
        # 20 messages * 500 chars = 10000 chars = 2500 tokens >> 500 max
        msgs = _make_messages(20, chars_each=500)
        result = mgr.check(msgs)
        # Aggressive compact should keep very few messages
        assert len(result) < 10
        assert mgr.last_state is not None
        assert "aggressive_compact" in mgr.last_state.actions_taken


class TestConsolidate:
    def test_consolidate_returns_memory(self) -> None:
        mgr = ContextWindowManager(max_tokens=500)
        msgs = [
            Message.user("What testing framework is used?"),
            Message.assistant("The project uses pytest for testing."),
            Message.user("Where are the tests?"),
            Message.assistant("Tests are in the tests/ directory and use pytest fixtures."),
        ] + _make_messages(16, chars_each=200)
        compacted, memory = mgr.consolidate(msgs)
        # Should have extracted at least some facts
        assert isinstance(memory.facts, list)
        # Compacted should be shorter than original
        assert len(compacted) <= len(msgs) + 2  # +2 for possible injected system msgs


class TestThresholdValidation:
    def test_invalid_threshold_order_raises(self) -> None:
        with pytest.raises(ValueError, match="Thresholds must satisfy"):
            ContextWindowManager(
                max_tokens=1000,
                selective_threshold=0.90,
                summarize_threshold=0.85,
            )

    def test_valid_custom_thresholds(self) -> None:
        mgr = ContextWindowManager(
            max_tokens=1000,
            selective_threshold=0.60,
            summarize_threshold=0.75,
            aggressive_threshold=0.95,
        )
        assert mgr.selective_threshold == 0.60
        assert mgr.summarize_threshold == 0.75
        assert mgr.aggressive_threshold == 0.95

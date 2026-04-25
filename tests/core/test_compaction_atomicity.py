"""Tests for atomicity-aware compaction."""
from __future__ import annotations

import pytest

from chimera.compaction.base import AtomicGroup, CompactionUrgency, CompactionView
from chimera.compaction.thresholds import InsufficientCompactionError, ThresholdCompaction
from chimera.compaction.prune import PruneCompaction
from chimera.types import Message, ToolCall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(role: str, content: str, tool_calls: list[ToolCall] | None = None) -> Message:
    return Message(role=role, content=content, tool_calls=tool_calls or [])


def _system(content: str = "You are helpful.") -> Message:
    return Message.system(content)


def _user(content: str) -> Message:
    return Message.user(content)


def _assistant(content: str, tool_calls: list[ToolCall] | None = None) -> Message:
    return Message.assistant(content, tool_calls=tool_calls or [])


def _tool(call_id: str, content: str) -> Message:
    return Message.tool(call_id, content)


def _tc(name: str = "bash") -> ToolCall:
    return ToolCall(id=f"tc_{name}", name=name, arguments={})


# ---------------------------------------------------------------------------
# AtomicGroup
# ---------------------------------------------------------------------------

class TestAtomicGroup:
    def test_size(self):
        g = AtomicGroup(2, 5, "tool_call")
        assert g.size == 4

    def test_single_message(self):
        g = AtomicGroup(0, 0, "system")
        assert g.size == 1


# ---------------------------------------------------------------------------
# CompactionView — auto-detection
# ---------------------------------------------------------------------------

class TestCompactionViewDetection:
    def test_detects_system_message(self):
        msgs = [_system(), _user("hi"), _assistant("hello")]
        view = CompactionView(msgs)
        system_groups = [g for g in view.atomic_groups if g.group_type == "system"]
        assert len(system_groups) == 1
        assert system_groups[0].start_index == 0

    def test_detects_tool_call_pairs(self):
        msgs = [
            _system(),
            _user("do something"),
            _assistant("I'll use bash", tool_calls=[_tc("bash")]),
            _tool("tc_bash", "output here"),
            _assistant("done"),
        ]
        view = CompactionView(msgs)
        tool_groups = [g for g in view.atomic_groups if g.group_type == "tool_call"]
        assert len(tool_groups) == 1
        assert tool_groups[0].start_index == 2
        assert tool_groups[0].end_index == 3

    def test_no_groups_when_no_patterns(self):
        msgs = [_user("hi"), _assistant("hello")]
        view = CompactionView(msgs)
        assert len(view.atomic_groups) == 0

    def test_multiple_tool_results(self):
        msgs = [
            _system(),
            _assistant("tools", tool_calls=[_tc("a"), _tc("b")]),
            _tool("tc_a", "out_a"),
            _tool("tc_b", "out_b"),
            _assistant("done"),
        ]
        view = CompactionView(msgs)
        tool_groups = [g for g in view.atomic_groups if g.group_type == "tool_call"]
        assert len(tool_groups) == 1
        assert tool_groups[0].start_index == 1
        assert tool_groups[0].end_index == 3


# ---------------------------------------------------------------------------
# CompactionView — safe indices
# ---------------------------------------------------------------------------

class TestCompactionViewSafeIndices:
    def test_system_and_last_protected(self):
        msgs = [_system(), _user("a"), _assistant("b"), _user("c")]
        view = CompactionView(msgs)
        safe = view.safe_removal_indices
        assert 0 not in safe  # system
        assert 3 not in safe  # last message

    def test_tool_pair_protected(self):
        msgs = [
            _system(),
            _user("do it"),
            _assistant("calling", tool_calls=[_tc()]),
            _tool("tc_bash", "result"),
            _user("ok"),
            _assistant("done"),
        ]
        view = CompactionView(msgs)
        safe = view.safe_removal_indices
        assert 2 not in safe  # tool call
        assert 3 not in safe  # tool result
        assert 1 in safe      # user message is safe

    def test_custom_atomic_groups(self):
        msgs = [_system(), _user("a"), _assistant("b"), _user("c"), _assistant("d")]
        view = CompactionView(msgs, atomic_groups=[
            AtomicGroup(0, 0, "system"),
            AtomicGroup(1, 2, "reasoning_chain"),
        ])
        safe = view.safe_removal_indices
        assert 0 not in safe
        assert 1 not in safe
        assert 2 not in safe
        assert 3 in safe
        assert 4 not in safe  # last message


# ---------------------------------------------------------------------------
# CompactionView — compact
# ---------------------------------------------------------------------------

class TestCompactionViewCompact:
    def test_removes_only_safe_indices(self):
        msgs = [_system(), _user("a"), _assistant("b"), _user("c"), _assistant("d")]
        view = CompactionView(msgs)
        # Try to remove everything
        result = view.compact([0, 1, 2, 3, 4])
        # System (0) and last (4) should be preserved
        assert len(result.messages) >= 2
        assert result.messages[0].role == "system"
        assert result.messages[-1] == msgs[4]

    def test_respects_tool_call_atomicity(self):
        msgs = [
            _system(),
            _user("do it"),
            _assistant("calling", tool_calls=[_tc()]),
            _tool("tc_bash", "result"),
            _assistant("done"),
        ]
        view = CompactionView(msgs)
        # Try removing tool call message
        result = view.compact([2, 3])
        # Tool call pair should be preserved
        assert any(m.content == "calling" for m in result.messages)
        assert any(m.content == "result" for m in result.messages)


# ---------------------------------------------------------------------------
# CompactionView — token_estimate
# ---------------------------------------------------------------------------

class TestCompactionViewTokenEstimate:
    def test_estimates_tokens(self):
        msgs = [_system("a" * 400)]  # 400 chars ≈ 100 tokens
        view = CompactionView(msgs)
        assert view.token_estimate == 100


# ---------------------------------------------------------------------------
# CompactionUrgency
# ---------------------------------------------------------------------------

class TestCompactionUrgency:
    def test_string_values(self):
        assert CompactionUrgency.NONE == "none"
        assert CompactionUrgency.SOFT == "soft"
        assert CompactionUrgency.HARD == "hard"


# ---------------------------------------------------------------------------
# ThresholdCompaction
# ---------------------------------------------------------------------------

class TestThresholdCompaction:
    def test_no_compaction_below_soft(self):
        strategy = PruneCompaction()
        tc = ThresholdCompaction(
            strategy=strategy, soft_threshold=0.7, hard_threshold=0.9,
            max_context_tokens=100000,
        )
        msgs = [_system("short")]
        view = CompactionView(msgs)
        result = tc.compact(view)
        assert result.messages == msgs

    def test_check_urgency_none(self):
        tc = ThresholdCompaction(
            strategy=PruneCompaction(), max_context_tokens=100000,
        )
        msgs = [_system("short")]
        view = CompactionView(msgs)
        assert tc.check_urgency(view) == CompactionUrgency.NONE

    def test_check_urgency_soft(self):
        tc = ThresholdCompaction(
            strategy=PruneCompaction(), max_context_tokens=100,
            soft_threshold=0.5,
        )
        # 60 chars ≈ 15 tokens, but we need > 50 tokens for soft at 100 max
        msgs = [_system("a" * 250)]  # 250/4 = 62 tokens, > 50
        view = CompactionView(msgs)
        assert tc.check_urgency(view) == CompactionUrgency.SOFT

    def test_check_urgency_hard(self):
        tc = ThresholdCompaction(
            strategy=PruneCompaction(), max_context_tokens=100,
            hard_threshold=0.5,
        )
        msgs = [_system("a" * 250)]
        view = CompactionView(msgs)
        assert tc.check_urgency(view) == CompactionUrgency.HARD

    def test_hard_reset_keeps_system_and_tail(self):
        tc = ThresholdCompaction(
            strategy=PruneCompaction(), max_context_tokens=10,
            hard_threshold=0.1, keep_last=2,
        )
        msgs = [
            _system("sys"),
            _user("a" * 100),
            _assistant("b" * 100),
            _user("c"),
            _assistant("d"),
        ]
        view = CompactionView(msgs)
        result = tc.compact(view)
        # Should have: system + marker + last 2
        assert result.messages[0].content == "sys"
        assert "[Previous context" in result.messages[1].content
        assert result.messages[-1].content == "d"
        assert result.messages[-2].content == "c"

    def test_soft_returns_original_when_cant_compact(self):
        strategy = PruneCompaction()
        tc = ThresholdCompaction(
            strategy=strategy, max_context_tokens=10,
            soft_threshold=0.1, hard_threshold=0.99,
        )
        # Single short system message - can't compact further
        msgs = [_system("x" * 50)]
        view = CompactionView(msgs)
        result = tc.compact(view)
        # Soft threshold: should return as-is since can't compact
        assert len(result.messages) >= 1

    def test_zero_max_tokens(self):
        tc = ThresholdCompaction(
            strategy=PruneCompaction(), max_context_tokens=0,
        )
        msgs = [_system("test")]
        view = CompactionView(msgs)
        assert tc.check_urgency(view) == CompactionUrgency.NONE


# ---------------------------------------------------------------------------
# InsufficientCompactionError
# ---------------------------------------------------------------------------

class TestInsufficientCompactionError:
    def test_is_exception(self):
        with pytest.raises(InsufficientCompactionError):
            raise InsufficientCompactionError("no safe points")

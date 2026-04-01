"""Tests for chimera.core.snip_compact — Snip and Micro Compaction."""
from __future__ import annotations

from chimera.core.snip_compact import MicroCompactor, SnipCompactor
from chimera.types import Message


class TestSnipCompactor:
    """SnipCompactor removes old tool results, keeping only the most recent N."""

    def test_no_snip_when_under_limit(self):
        msgs = [
            Message.user("hello"),
            Message.tool("c1", "result1"),
            Message.tool("c2", "result2"),
        ]
        compactor = SnipCompactor(max_tool_results_to_keep=5)
        result, snipped = compactor.snip_if_needed(msgs)
        assert snipped is False
        assert len(result) == 3

    def test_snip_removes_old_tool_results(self):
        msgs = [
            Message.user("hello"),
            Message.tool("c1", "old1"),
            Message.tool("c2", "old2"),
            Message.tool("c3", "old3"),
            Message.assistant("thinking"),
            Message.tool("c4", "recent1"),
            Message.tool("c5", "recent2"),
        ]
        compactor = SnipCompactor(max_tool_results_to_keep=2)
        result, snipped = compactor.snip_if_needed(msgs)
        assert snipped is True
        # Should have removed 3 old tool results, keeping 2 recent + 2 non-tool
        tool_msgs = [m for m in result if m.role == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0].content == "recent1"
        assert tool_msgs[1].content == "recent2"

    def test_non_tool_messages_preserved(self):
        msgs = [
            Message.user("hello"),
            Message.tool("c1", "old1"),
            Message.tool("c2", "old2"),
            Message.tool("c3", "old3"),
            Message.assistant("thinking"),
            Message.tool("c4", "recent1"),
        ]
        compactor = SnipCompactor(max_tool_results_to_keep=1)
        result, snipped = compactor.snip_if_needed(msgs)
        assert snipped is True
        non_tool = [m for m in result if m.role != "tool"]
        assert len(non_tool) == 2  # user + assistant preserved

    def test_empty_messages(self):
        compactor = SnipCompactor(max_tool_results_to_keep=5)
        result, snipped = compactor.snip_if_needed([])
        assert snipped is False
        assert result == []


class TestMicroCompactor:
    """MicroCompactor truncates messages exceeding max_message_chars."""

    def test_no_compaction_under_limit(self):
        msgs = [Message.user("short message")]
        compactor = MicroCompactor(max_message_chars=1000)
        result, compacted = compactor.compact(msgs)
        assert compacted is False
        assert result[0].content == "short message"

    def test_truncates_long_message(self):
        long_content = "x" * 100_000
        msgs = [Message.user(long_content)]
        compactor = MicroCompactor(max_message_chars=50_000)
        result, compacted = compactor.compact(msgs)
        assert compacted is True
        assert len(result[0].content) < len(long_content)
        assert "chars removed" in result[0].content

    def test_preserves_start_and_end(self):
        content = "START" + "x" * 100_000 + "END"
        msgs = [Message.user(content)]
        compactor = MicroCompactor(max_message_chars=50_000)
        result, compacted = compactor.compact(msgs)
        assert compacted is True
        assert result[0].content.startswith("START")
        assert result[0].content.endswith("END")

    def test_does_not_mutate_original(self):
        long_content = "x" * 100_000
        msg = Message.user(long_content)
        msgs = [msg]
        compactor = MicroCompactor(max_message_chars=50_000)
        result, _ = compactor.compact(msgs)
        # Original should still have full content
        assert len(msg.content) == 100_000
        assert len(result[0].content) < 100_000

    def test_empty_messages(self):
        compactor = MicroCompactor(max_message_chars=1000)
        result, compacted = compactor.compact([])
        assert compacted is False
        assert result == []

    def test_multiple_messages_mixed(self):
        msgs = [
            Message.user("short"),
            Message.assistant("x" * 100_000),
            Message.user("also short"),
        ]
        compactor = MicroCompactor(max_message_chars=50_000)
        result, compacted = compactor.compact(msgs)
        assert compacted is True
        assert result[0].content == "short"
        assert len(result[1].content) < 100_000
        assert result[2].content == "also short"

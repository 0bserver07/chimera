# tests/test_compression.py
from chimera.core.compression import ContextCompressor
from chimera.types import Message


class TestContextCompressor:
    def test_no_compression_under_limit(self):
        c = ContextCompressor(max_messages=10)
        messages = [Message.user(f"msg {i}") for i in range(5)]
        result = c.compress(messages)
        assert len(result) == 5

    def test_compress_over_limit_keeps_first_and_last(self):
        c = ContextCompressor(max_messages=4, keep_first=1, keep_last=2)
        messages = [Message.user(f"msg {i}") for i in range(10)]
        result = c.compress(messages)
        assert len(result) == 4  # 1 first + 1 summary + 2 last
        assert result[0].content == "msg 0"  # First kept
        assert result[-1].content == "msg 9"  # Last kept
        assert result[-2].content == "msg 8"  # Second-to-last kept

    def test_compress_includes_summary(self):
        c = ContextCompressor(max_messages=4, keep_first=1, keep_last=1)
        messages = [Message.user(f"msg {i}") for i in range(10)]
        result = c.compress(messages)
        # Middle message should be a summary
        summary = result[1]
        assert summary.role == "system" or "summarized" in summary.content.lower() or "compressed" in summary.content.lower()

    def test_tool_messages_compressed(self):
        c = ContextCompressor(max_messages=4, keep_first=1, keep_last=1)
        messages = [
            Message.user("Do something"),
            Message.assistant("I'll read the file", tool_calls=[]),
            Message.tool("call_1", "A" * 10000),  # Large tool output
            Message.user("Thanks"),
        ]
        result = c.compress(messages)
        assert len(result) <= 4

    def test_compress_empty(self):
        c = ContextCompressor(max_messages=10)
        result = c.compress([])
        assert result == []

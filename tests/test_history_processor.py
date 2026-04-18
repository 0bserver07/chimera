from chimera.context.history import (
    TruncateProcessor, PruneProcessor, CompressProcessor, CompositeProcessor,
)
from chimera.types import Message


def test_truncate_keeps_last_n():
    msgs = [Message.user(f"msg {i}") for i in range(10)]
    proc = TruncateProcessor(max_messages=3)
    result = proc.process(msgs)
    assert len(result) == 3
    assert result[0].content == "msg 7"


def test_truncate_short_list():
    msgs = [Message.user("a"), Message.user("b")]
    proc = TruncateProcessor(max_messages=5)
    result = proc.process(msgs)
    assert len(result) == 2


def test_prune_removes_old_tool_results():
    msgs = [
        Message.user("q1"),
        Message(role="tool", content="long result 1"),
        Message.user("q2"),
        Message(role="tool", content="long result 2"),
        Message.user("q3"),
        Message(role="tool", content="long result 3"),
    ]
    proc = PruneProcessor(keep_last_n_results=1)
    result = proc.process(msgs)
    # First 2 tool messages should be pruned
    tool_msgs = [m for m in result if m.role == "tool"]
    pruned = [m for m in tool_msgs if m.content == "[pruned]"]
    assert len(pruned) == 2
    # Last tool message kept
    assert tool_msgs[-1].content == "long result 3"


def test_prune_preserves_call_id_on_pruned_tool_messages():
    """Regression: Anthropic/OpenAI require tool_call_id on tool messages.

    Previously PruneProcessor created replacement tool messages with
    ``content='[pruned]'`` but no ``call_id``, producing histories that
    real providers reject because every tool result must match a prior
    assistant tool_call by id.
    """
    msgs = [
        Message.user("q1"),
        Message.tool("call_a", "result A"),
        Message.user("q2"),
        Message.tool("call_b", "result B"),
        Message.user("q3"),
        Message.tool("call_c", "result C"),
    ]
    proc = PruneProcessor(keep_last_n_results=1)
    result = proc.process(msgs)
    tool_msgs = [m for m in result if m.role == "tool"]
    # First 2 tool messages pruned but must retain their call_ids
    assert tool_msgs[0].content == "[pruned]"
    assert tool_msgs[0].call_id == "call_a"
    assert tool_msgs[1].content == "[pruned]"
    assert tool_msgs[1].call_id == "call_b"
    # Last tool message intact
    assert tool_msgs[2].content == "result C"
    assert tool_msgs[2].call_id == "call_c"


def test_compress_summarizes_old():
    msgs = [Message.user(f"turn {i}") for i in range(8)]
    proc = CompressProcessor(keep_recent=3)
    result = proc.process(msgs)
    assert len(result) == 4  # 1 summary + 3 recent
    assert "summary" in result[0].content.lower()


def test_composite_chains():
    msgs = [Message.user(f"msg {i}") for i in range(20)]
    proc = CompositeProcessor([
        TruncateProcessor(max_messages=10),
        CompressProcessor(keep_recent=3),
    ])
    result = proc.process(msgs)
    assert len(result) <= 4  # compressed


def test_empty_history():
    proc = TruncateProcessor()
    assert proc.process([]) == []

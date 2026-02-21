from chimera.providers.base import Provider, Response, StreamEvent
from chimera.types import Message, ToolCall


def test_response_dataclass():
    r = Response(
        content="hello",
        tool_calls=[ToolCall(id="1", name="read", arguments={"path": "x"})],
        usage={"input_tokens": 100, "output_tokens": 50},
    )
    assert r.content == "hello"
    assert len(r.tool_calls) == 1
    assert r.usage["input_tokens"] == 100


def test_response_no_tool_calls():
    r = Response(content="done", tool_calls=[], usage={})
    assert r.has_tool_calls is False


def test_response_with_tool_calls():
    r = Response(
        content="",
        tool_calls=[ToolCall(id="1", name="x", arguments={})],
        usage={},
    )
    assert r.has_tool_calls is True


def test_stream_event():
    e = StreamEvent(type="text_delta", content="hi")
    assert e.type == "text_delta"

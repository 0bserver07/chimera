"""Tests for StreamingReAct._accumulate_stream and streaming loop."""
from __future__ import annotations

from chimera.providers.base import StreamEvent
from chimera.streaming.loop import StreamingReAct
from chimera.types import ToolCall


class TestAccumulateStream:
    def test_text_only(self) -> None:
        events = [
            StreamEvent(type="text_delta", content="Hello "),
            StreamEvent(type="text_delta", content="world"),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5}),
        ]
        resp = StreamingReAct._accumulate_stream(iter(events), None)
        assert resp.content == "Hello world"
        assert resp.tool_calls == []
        assert resp.usage == {"input_tokens": 10, "output_tokens": 5}

    def test_tool_call_complete_sequence(self) -> None:
        tc = ToolCall(id="call_1", name="bash", arguments={"command": "ls"})
        events = [
            StreamEvent(type="tool_call_start", tool_call=ToolCall(id="call_1", name="bash", arguments={})),
            StreamEvent(type="tool_call_delta", content='{"command": "ls"}'),
            StreamEvent(type="tool_call_complete", tool_call=tc),
            StreamEvent(type="done", usage={"input_tokens": 20, "output_tokens": 10}),
        ]
        resp = StreamingReAct._accumulate_stream(iter(events), None)
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "bash"
        assert resp.tool_calls[0].arguments == {"command": "ls"}

    def test_mixed_text_and_tool_call(self) -> None:
        tc = ToolCall(id="call_1", name="read_file", arguments={"path": "x.py"})
        events = [
            StreamEvent(type="text_delta", content="Let me read that"),
            StreamEvent(type="tool_call_start", tool_call=ToolCall(id="call_1", name="read_file", arguments={})),
            StreamEvent(type="tool_call_complete", tool_call=tc),
            StreamEvent(type="done", usage={"input_tokens": 15, "output_tokens": 8}),
        ]
        resp = StreamingReAct._accumulate_stream(iter(events), None)
        assert resp.content == "Let me read that"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].arguments == {"path": "x.py"}

    def test_usage_from_done_event(self) -> None:
        events = [
            StreamEvent(type="text_delta", content="ok"),
            StreamEvent(type="done", usage={"input_tokens": 100, "output_tokens": 50}),
        ]
        resp = StreamingReAct._accumulate_stream(iter(events), None)
        assert resp.usage == {"input_tokens": 100, "output_tokens": 50}

    def test_usage_defaults_without_done_usage(self) -> None:
        events = [
            StreamEvent(type="text_delta", content="ok"),
            StreamEvent(type="done"),
        ]
        resp = StreamingReAct._accumulate_stream(iter(events), None)
        assert resp.usage == {"input_tokens": 0, "output_tokens": 0}

    def test_fallback_tool_call_via_done(self) -> None:
        """Default Provider.stream() sends tool_call_start + done (no complete)."""
        tc = ToolCall(id="call_1", name="bash", arguments={"command": "ls"})
        events = [
            StreamEvent(type="tool_call_start", tool_call=tc),
            StreamEvent(type="done", usage={"input_tokens": 5, "output_tokens": 3}),
        ]
        resp = StreamingReAct._accumulate_stream(iter(events), None)
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0] == tc

    def test_multiple_tool_calls(self) -> None:
        tc1 = ToolCall(id="c1", name="bash", arguments={"command": "ls"})
        tc2 = ToolCall(id="c2", name="read_file", arguments={"path": "x.py"})
        events = [
            StreamEvent(type="tool_call_start", tool_call=ToolCall(id="c1", name="bash", arguments={})),
            StreamEvent(type="tool_call_complete", tool_call=tc1),
            StreamEvent(type="tool_call_start", tool_call=ToolCall(id="c2", name="read_file", arguments={})),
            StreamEvent(type="tool_call_complete", tool_call=tc2),
            StreamEvent(type="done", usage={"input_tokens": 30, "output_tokens": 20}),
        ]
        resp = StreamingReAct._accumulate_stream(iter(events), None)
        assert len(resp.tool_calls) == 2
        assert resp.tool_calls[0].name == "bash"
        assert resp.tool_calls[1].name == "read_file"

"""Tests for ReAct loop with streaming handler."""
from __future__ import annotations

from chimera.core.context import Context
from chimera.core.loop import ReAct, drain_steps
from chimera.core.loop_config import LoopConfig
from chimera.providers.base import Provider, Response, StreamEvent
from chimera.streaming.handlers import CollectStreamHandler
from chimera.types import ToolCall


class FakeStreamProvider(Provider):
    """Provider that yields streaming events."""

    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        self._responses = responses
        self._call = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        raise AssertionError("complete() should not be called when streaming")

    def stream(self, messages, tools=None, temperature=0.0, max_tokens=None):
        events = self._responses[self._call]
        self._call += 1
        yield from events

    @property
    def model_name(self) -> str:
        return "test-model"

    @property
    def context_window(self) -> int:
        return 4096

    @property
    def supports_tool_use(self) -> bool:
        return True


class FakeCompleteProvider(Provider):
    """Provider that only supports complete(), not stream()."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = responses
        self._call = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        resp = self._responses[self._call]
        self._call += 1
        return resp

    @property
    def model_name(self) -> str:
        return "test-model"

    @property
    def context_window(self) -> int:
        return 4096

    @property
    def supports_tool_use(self) -> bool:
        return True


class TestReActStreaming:
    def test_text_streams_to_handler(self):
        """When handler is set, text deltas flow through it."""
        events = [
            StreamEvent(type="text_delta", content="Hello "),
            StreamEvent(type="text_delta", content="world"),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5}),
        ]
        provider = FakeStreamProvider([events])
        handler = CollectStreamHandler()
        loop = ReAct(config=LoopConfig(handler=handler))

        result = drain_steps(loop.iter_steps(provider, [], Context(), None))

        assert result.success
        assert result.output == "Hello world"
        text_events = [e for e in handler.events if e["type"] == "text"]
        assert len(text_events) == 2
        assert text_events[0]["content"] == "Hello "
        assert text_events[1]["content"] == "world"

    def test_no_handler_uses_complete(self):
        """When handler is None, provider.complete() is used (not stream)."""
        provider = FakeCompleteProvider([
            Response(content="Hi", tool_calls=[], usage={"input_tokens": 5, "output_tokens": 2}),
        ])
        loop = ReAct()  # No config, no handler

        result = drain_steps(loop.iter_steps(provider, [], Context(), None))

        assert result.success
        assert result.output == "Hi"

    def test_handler_gets_step_events(self):
        """Handler receives step_start, step_end, and done events."""
        events = [
            StreamEvent(type="text_delta", content="ok"),
            StreamEvent(type="done", usage={"input_tokens": 1, "output_tokens": 1}),
        ]
        provider = FakeStreamProvider([events])
        handler = CollectStreamHandler()
        loop = ReAct(config=LoopConfig(handler=handler))

        drain_steps(loop.iter_steps(provider, [], Context(), None))

        types = [e["type"] for e in handler.events]
        assert "step_start" in types
        assert "step_end" in types
        assert "done" in types

    def test_handler_gets_tool_events(self):
        """Handler receives tool_start and tool_end for tool calls."""
        from chimera.core.tool import tool as tool_decorator
        from chimera.types import ToolResult

        @tool_decorator(name="greet", description="Say hello", parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        })
        def greet(args, env):
            return ToolResult(output=f"Hello {args['name']}")

        tc = ToolCall(id="c1", name="greet", arguments={"name": "Alice"})
        # Step 1: tool call
        step1_events = [
            StreamEvent(type="tool_call_start", tool_call=tc),
            StreamEvent(type="tool_call_complete", tool_call=tc),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5}),
        ]
        # Step 2: final text
        step2_events = [
            StreamEvent(type="text_delta", content="Done"),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 3}),
        ]
        provider = FakeStreamProvider([step1_events, step2_events])
        handler = CollectStreamHandler()
        loop = ReAct(config=LoopConfig(handler=handler))

        result = drain_steps(loop.iter_steps(provider, [greet], Context(), None))

        assert result.success
        types = [e["type"] for e in handler.events]
        assert "tool_start" in types
        assert "tool_end" in types

    def test_accumulate_stream_static(self):
        """ReAct._accumulate_stream works identically to StreamingReAct's."""
        events = [
            StreamEvent(type="text_delta", content="Hello "),
            StreamEvent(type="text_delta", content="world"),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5}),
        ]
        resp = ReAct._accumulate_stream(iter(events), None)
        assert resp.content == "Hello world"
        assert resp.usage == {"input_tokens": 10, "output_tokens": 5}

    def test_streaming_preserves_permissions(self):
        """Streaming + permissions work together (the whole point of the merge)."""
        from chimera.permissions.base import PermissionPolicy, PermissionAction

        class DenyBash(PermissionPolicy):
            def evaluate(self, tool_name, arguments):
                return PermissionAction.DENY if tool_name == "bash" else PermissionAction.ALLOW

        from chimera.core.tool import tool as tool_decorator
        from chimera.types import ToolResult

        @tool_decorator(name="bash", description="Run command", parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        })
        def bash(args, env):
            return ToolResult(output="output")

        tc = ToolCall(id="c1", name="bash", arguments={"command": "ls"})
        step1_events = [
            StreamEvent(type="tool_call_start", tool_call=tc),
            StreamEvent(type="tool_call_complete", tool_call=tc),
            StreamEvent(type="done", usage={"input_tokens": 10, "output_tokens": 5}),
        ]
        step2_events = [
            StreamEvent(type="text_delta", content="OK"),
            StreamEvent(type="done", usage={"input_tokens": 5, "output_tokens": 2}),
        ]
        provider = FakeStreamProvider([step1_events, step2_events])
        handler = CollectStreamHandler()
        loop = ReAct(config=LoopConfig(handler=handler, permissions=DenyBash()))

        result = drain_steps(loop.iter_steps(provider, [bash], Context(), None))

        assert result.success
        # bash was denied but the loop continued

    def test_iter_steps_yields_with_handler(self):
        """iter_steps still yields StepResult even with streaming."""
        events = [
            StreamEvent(type="text_delta", content="answer"),
            StreamEvent(type="done", usage={"input_tokens": 5, "output_tokens": 3}),
        ]
        provider = FakeStreamProvider([events])
        handler = CollectStreamHandler()
        loop = ReAct(config=LoopConfig(handler=handler))

        steps = list(loop.iter_steps(provider, [], Context(), None))
        assert len(steps) == 1
        assert steps[0].done is True
        assert steps[0].message.content == "answer"

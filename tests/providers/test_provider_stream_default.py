"""Tests for Provider.stream() default implementation."""
from __future__ import annotations

from chimera.providers.base import Provider, Response, StreamEvent, ToolSchema
from chimera.types import Message, ToolCall


class MinimalProvider(Provider):
    """Provider that only implements complete() — no stream() override."""

    def __init__(self, response: Response) -> None:
        self._response = response

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        **kwargs,
    ) -> Response:
        return self._response

    @property
    def context_window(self) -> int:
        return 4096

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "test-model"


class TestStreamEventUsage:
    def test_default_none(self) -> None:
        event = StreamEvent(type="text_delta", content="hi")
        assert event.usage is None

    def test_with_usage(self) -> None:
        usage = {"input_tokens": 10, "output_tokens": 5}
        event = StreamEvent(type="done", usage=usage)
        assert event.usage == usage

    def test_backward_compat(self) -> None:
        """StreamEvent without usage kwarg still works."""
        event = StreamEvent(type="done")
        assert event.usage is None
        assert event.content == ""
        assert event.tool_call is None


class TestProviderStreamDefault:
    def test_text_only_response(self) -> None:
        response = Response(
            content="Hello world",
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        provider = MinimalProvider(response)
        events = list(provider.stream([Message.user("hi")]))

        assert len(events) == 2
        assert events[0].type == "text_delta"
        assert events[0].content == "Hello world"
        assert events[1].type == "done"
        assert events[1].usage == {"input_tokens": 10, "output_tokens": 5}

    def test_tool_call_response(self) -> None:
        tc = ToolCall(id="call_1", name="read_file", arguments={"path": "foo.py"})
        response = Response(
            content="",
            tool_calls=[tc],
            usage={"input_tokens": 20, "output_tokens": 10},
        )
        provider = MinimalProvider(response)
        events = list(provider.stream([Message.user("read foo.py")]))

        # No text_delta (content is empty), one tool_call_start, one done
        assert len(events) == 2
        assert events[0].type == "tool_call_start"
        assert events[0].tool_call == tc
        assert events[1].type == "done"
        assert events[1].usage == {"input_tokens": 20, "output_tokens": 10}

    def test_mixed_text_and_tool_calls(self) -> None:
        tc1 = ToolCall(id="call_1", name="bash", arguments={"command": "ls"})
        tc2 = ToolCall(id="call_2", name="read_file", arguments={"path": "x.py"})
        response = Response(
            content="I'll help you",
            tool_calls=[tc1, tc2],
            usage={"input_tokens": 30, "output_tokens": 15},
        )
        provider = MinimalProvider(response)
        events = list(provider.stream([Message.user("help")]))

        assert len(events) == 4
        assert events[0].type == "text_delta"
        assert events[0].content == "I'll help you"
        assert events[1].type == "tool_call_start"
        assert events[1].tool_call == tc1
        assert events[2].type == "tool_call_start"
        assert events[2].tool_call == tc2
        assert events[3].type == "done"

    def test_done_carries_usage(self) -> None:
        usage = {"input_tokens": 100, "output_tokens": 50}
        response = Response(content="ok", tool_calls=[], usage=usage)
        provider = MinimalProvider(response)
        events = list(provider.stream([Message.user("test")]))
        done_event = events[-1]
        assert done_event.type == "done"
        assert done_event.usage == usage

    def test_stream_is_iterator(self) -> None:
        response = Response(content="hi", tool_calls=[], usage={"input_tokens": 1, "output_tokens": 1})
        provider = MinimalProvider(response)
        stream = provider.stream([Message.user("test")])
        # Should be iterable via next()
        event = next(stream)
        assert event.type == "text_delta"

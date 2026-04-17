"""Tests for AnthropicProvider.stream() with mocked Anthropic SDK."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chimera.types import Message


# ---------------------------------------------------------------------------
# Lightweight fakes for the Anthropic SDK objects
# ---------------------------------------------------------------------------

@dataclass
class FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None


@dataclass
class FakeTextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class FakeToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeMessage:
    content: list[Any] = field(default_factory=list)
    usage: FakeUsage = field(default_factory=FakeUsage)


# Stream event fakes
@dataclass
class FakeContentBlockStart:
    type: str = "content_block_start"
    content_block: Any = None


@dataclass
class FakeContentBlockDelta:
    type: str = "content_block_delta"
    delta: Any = None


@dataclass
class FakeContentBlockStop:
    type: str = "content_block_stop"


@dataclass
class FakeTextDelta:
    type: str = "text_delta"
    text: str = ""


@dataclass
class FakeInputJsonDelta:
    type: str = "input_json_delta"
    partial_json: str = ""


class FakeStream:
    """Fake for the context manager returned by client.messages.stream()."""

    def __init__(self, events: list[Any], final_message: FakeMessage) -> None:
        self._events = events
        self._final = final_message

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self) -> FakeMessage:
        return self._final


class FakeAsyncStream:
    """Fake for the async context manager returned by aclient.messages.stream()."""

    def __init__(self, events: list[Any], final_message: FakeMessage) -> None:
        self._events = events
        self._final = final_message

    async def __aenter__(self) -> FakeAsyncStream:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def __aiter__(self):
        for event in self._events:
            yield event

    async def get_final_message(self) -> FakeMessage:
        return self._final


# ---------------------------------------------------------------------------
# Fixture: provider with mocked client
# ---------------------------------------------------------------------------

@pytest.fixture()
def provider():
    with patch.dict("sys.modules", {"anthropic": MagicMock()}):
        from chimera.providers.anthropic import AnthropicProvider
        p = AnthropicProvider.__new__(AnthropicProvider)
        p._model = "claude-sonnet-4"
        p._client = MagicMock()
        p._enable_cache = False
        p._enable_thinking = False
        p._thinking_budget = 10_000
        return p


# ---------------------------------------------------------------------------
# Tests: _prepare_request
# ---------------------------------------------------------------------------

class TestPrepareRequest:
    def test_basic(self, provider) -> None:
        msgs = [Message.user("hello")]
        kwargs = provider._prepare_request(msgs)
        assert kwargs["model"] == "claude-sonnet-4"
        assert kwargs["max_tokens"] == 4096
        assert len(kwargs["messages"]) == 1

    def test_system_extracted(self, provider) -> None:
        msgs = [Message.system("You are helpful"), Message.user("hi")]
        kwargs = provider._prepare_request(msgs)
        assert kwargs["system"] == "You are helpful"
        assert len(kwargs["messages"]) == 1


# ---------------------------------------------------------------------------
# Tests: _parse_response
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_text_only(self, provider) -> None:
        resp = FakeMessage(
            content=[FakeTextBlock(text="Hello!")],
            usage=FakeUsage(input_tokens=10, output_tokens=5),
        )
        result = provider._parse_response(resp)
        assert result.content == "Hello!"
        assert result.tool_calls == []
        assert result.usage == {"input_tokens": 10, "output_tokens": 5}

    def test_tool_use(self, provider) -> None:
        resp = FakeMessage(
            content=[FakeToolUseBlock(id="c1", name="bash", input={"command": "ls"})],
            usage=FakeUsage(),
        )
        result = provider._parse_response(resp)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "bash"


# ---------------------------------------------------------------------------
# Tests: complete() refactored
# ---------------------------------------------------------------------------

class TestCompleteRefactored:
    def test_delegates_to_helpers(self, provider) -> None:
        fake_resp = FakeMessage(
            content=[FakeTextBlock(text="ok")],
            usage=FakeUsage(input_tokens=5, output_tokens=2),
        )
        provider._client.messages.create.return_value = fake_resp
        result = provider.complete([Message.user("test")])
        assert result.content == "ok"
        provider._client.messages.create.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: stream()
# ---------------------------------------------------------------------------

class TestAnthropicStream:
    def test_text_only_streaming(self, provider) -> None:
        events = [
            FakeContentBlockStart(content_block=FakeTextBlock()),
            FakeContentBlockDelta(delta=FakeTextDelta(text="Hello ")),
            FakeContentBlockDelta(delta=FakeTextDelta(text="world")),
            FakeContentBlockStop(),
        ]
        final = FakeMessage(
            content=[FakeTextBlock(text="Hello world")],
            usage=FakeUsage(input_tokens=10, output_tokens=5),
        )
        provider._client.messages.stream.return_value = FakeStream(events, final)

        stream_events = list(provider.stream([Message.user("hi")]))
        types = [e.type for e in stream_events]

        assert "text_delta" in types
        assert types[-1] == "done"
        done = stream_events[-1]
        assert done.usage == {"input_tokens": 10, "output_tokens": 5}

    def test_tool_use_streaming(self, provider) -> None:
        tool_block = FakeToolUseBlock(id="call_1", name="read_file")
        events = [
            FakeContentBlockStart(content_block=tool_block),
            FakeContentBlockDelta(delta=FakeInputJsonDelta(partial_json='{"path"')),
            FakeContentBlockDelta(delta=FakeInputJsonDelta(partial_json=': "foo.py"}')),
            FakeContentBlockStop(),
        ]
        final = FakeMessage(
            content=[FakeToolUseBlock(id="call_1", name="read_file", input={"path": "foo.py"})],
            usage=FakeUsage(input_tokens=20, output_tokens=10),
        )
        provider._client.messages.stream.return_value = FakeStream(events, final)

        stream_events = list(provider.stream([Message.user("read foo.py")]))
        types = [e.type for e in stream_events]

        assert "tool_call_start" in types
        assert "tool_call_delta" in types
        assert "tool_call_complete" in types
        assert types[-1] == "done"

        # Verify the completed tool call has parsed args
        complete_event = [e for e in stream_events if e.type == "tool_call_complete"][0]
        assert complete_event.tool_call is not None
        assert complete_event.tool_call.name == "read_file"
        assert complete_event.tool_call.arguments == {"path": "foo.py"}

    def test_stream_includes_cache_tokens_in_done_usage(self, provider) -> None:
        """Regression: stream() must forward cache tokens in the final 'done' event.

        Previously only input/output tokens were surfaced, silently dropping
        prompt-cache accounting for streaming calls.
        """
        events = [
            FakeContentBlockStart(content_block=FakeTextBlock()),
            FakeContentBlockDelta(delta=FakeTextDelta(text="hi")),
            FakeContentBlockStop(),
        ]
        final = FakeMessage(
            content=[FakeTextBlock(text="hi")],
            usage=FakeUsage(
                input_tokens=100,
                output_tokens=5,
                cache_creation_input_tokens=80,
                cache_read_input_tokens=20,
            ),
        )
        provider._client.messages.stream.return_value = FakeStream(events, final)

        events_out = list(provider.stream([Message.user("hi")]))
        done = events_out[-1]
        assert done.type == "done"
        assert done.usage["input_tokens"] == 100
        assert done.usage["cache_creation_input_tokens"] == 80
        assert done.usage["cache_read_input_tokens"] == 20

    def test_mixed_text_and_tool_use(self, provider) -> None:
        text_block = FakeTextBlock()
        tool_block = FakeToolUseBlock(id="call_1", name="bash")
        events = [
            FakeContentBlockStart(content_block=text_block),
            FakeContentBlockDelta(delta=FakeTextDelta(text="I'll run ls")),
            FakeContentBlockStop(),
            FakeContentBlockStart(content_block=tool_block),
            FakeContentBlockDelta(delta=FakeInputJsonDelta(partial_json='{"command": "ls"}')),
            FakeContentBlockStop(),
        ]
        final = FakeMessage(
            content=[
                FakeTextBlock(text="I'll run ls"),
                FakeToolUseBlock(id="call_1", name="bash", input={"command": "ls"}),
            ],
            usage=FakeUsage(input_tokens=30, output_tokens=15),
        )
        provider._client.messages.stream.return_value = FakeStream(events, final)

        stream_events = list(provider.stream([Message.user("list files")]))
        types = [e.type for e in stream_events]

        assert "text_delta" in types
        assert "tool_call_start" in types
        assert "tool_call_complete" in types
        assert types[-1] == "done"


# ---------------------------------------------------------------------------
# Fixture: provider with mocked async client
# ---------------------------------------------------------------------------

@pytest.fixture()
def async_provider():
    with patch.dict("sys.modules", {"anthropic": MagicMock()}):
        from chimera.providers.anthropic import AnthropicProvider
        p = AnthropicProvider.__new__(AnthropicProvider)
        p._model = "claude-sonnet-4"
        p._client = MagicMock()
        p._enable_cache = False
        p._enable_thinking = False
        p._thinking_budget = 10_000
        p._async_client = MagicMock()
        p._async_client.messages.create = AsyncMock()
        return p


# ---------------------------------------------------------------------------
# Tests: async_complete
# ---------------------------------------------------------------------------

class TestAnthropicAsyncComplete:
    @pytest.mark.asyncio
    async def test_native_async_complete(self, async_provider) -> None:
        fake_resp = FakeMessage(
            content=[FakeTextBlock(text="async hello")],
            usage=FakeUsage(input_tokens=10, output_tokens=5),
        )
        async_provider._async_client.messages.create.return_value = fake_resp
        result = await async_provider.async_complete([Message.user("hi")])
        assert result.content == "async hello"
        assert result.usage == {"input_tokens": 10, "output_tokens": 5}


# ---------------------------------------------------------------------------
# Tests: async_stream
# ---------------------------------------------------------------------------

class TestAnthropicAsyncStream:
    @pytest.mark.asyncio
    async def test_native_async_stream_text(self, async_provider) -> None:
        events = [
            FakeContentBlockStart(content_block=FakeTextBlock()),
            FakeContentBlockDelta(delta=FakeTextDelta(text="async world")),
            FakeContentBlockStop(),
        ]
        final = FakeMessage(
            content=[FakeTextBlock(text="async world")],
            usage=FakeUsage(input_tokens=15, output_tokens=8),
        )
        async_provider._async_client.messages.stream.return_value = FakeAsyncStream(events, final)

        stream_events = []
        async for event in async_provider.async_stream([Message.user("hi")]):
            stream_events.append(event)

        types = [e.type for e in stream_events]
        assert "text_delta" in types
        assert types[-1] == "done"
        assert stream_events[-1].usage == {"input_tokens": 15, "output_tokens": 8}

    @pytest.mark.asyncio
    async def test_native_async_stream_tool_use(self, async_provider) -> None:
        tool_block = FakeToolUseBlock(id="call_1", name="bash")
        events = [
            FakeContentBlockStart(content_block=tool_block),
            FakeContentBlockDelta(delta=FakeInputJsonDelta(partial_json='{"command": "ls"}')),
            FakeContentBlockStop(),
        ]
        final = FakeMessage(
            content=[FakeToolUseBlock(id="call_1", name="bash", input={"command": "ls"})],
            usage=FakeUsage(input_tokens=20, output_tokens=10),
        )
        async_provider._async_client.messages.stream.return_value = FakeAsyncStream(events, final)

        stream_events = []
        async for event in async_provider.async_stream([Message.user("run ls")]):
            stream_events.append(event)

        types = [e.type for e in stream_events]
        assert "tool_call_start" in types
        assert "tool_call_complete" in types
        assert types[-1] == "done"

"""Tests for OpenAIProvider.stream() with mocked OpenAI SDK."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chimera.types import Message


# ---------------------------------------------------------------------------
# Lightweight fakes for the OpenAI SDK objects
# ---------------------------------------------------------------------------

@dataclass
class FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 5


@dataclass
class FakeFunction:
    name: str | None = None
    arguments: str | None = None


@dataclass
class FakeToolCallDelta:
    index: int = 0
    id: str | None = None
    function: FakeFunction | None = None


@dataclass
class FakeDelta:
    content: str | None = None
    tool_calls: list[FakeToolCallDelta] | None = None


@dataclass
class FakeChoice:
    delta: FakeDelta | None = None
    finish_reason: str | None = None
    message: Any = None  # For non-streaming


@dataclass
class FakeChunk:
    choices: list[FakeChoice] = field(default_factory=list)
    usage: FakeUsage | None = None


@dataclass
class FakeNonStreamMessage:
    content: str | None = None
    tool_calls: list[Any] | None = None


@dataclass
class FakeNonStreamChoice:
    message: FakeNonStreamMessage = field(default_factory=FakeNonStreamMessage)


@dataclass
class FakeNonStreamResponse:
    choices: list[FakeNonStreamChoice] = field(default_factory=list)
    usage: FakeUsage = field(default_factory=FakeUsage)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def provider():
    with patch.dict("sys.modules", {"openai": MagicMock()}):
        from chimera.providers.openai import OpenAIProvider
        p = OpenAIProvider.__new__(OpenAIProvider)
        p._model = "gpt-4o"
        p._client = MagicMock()
        return p


# ---------------------------------------------------------------------------
# Tests: _prepare_request
# ---------------------------------------------------------------------------

class TestPrepareRequest:
    def test_basic(self, provider) -> None:
        msgs = [Message.user("hello")]
        kwargs = provider._prepare_request(msgs)
        assert kwargs["model"] == "gpt-4o"
        assert kwargs["temperature"] == 0.0
        assert len(kwargs["messages"]) == 1

    def test_with_tools(self, provider) -> None:
        tools = [{"name": "bash", "description": "run command", "input_schema": {"type": "object"}}]
        kwargs = provider._prepare_request([Message.user("hi")], tools=tools)
        assert "tools" in kwargs


# ---------------------------------------------------------------------------
# Tests: complete() refactored
# ---------------------------------------------------------------------------

class TestCompleteRefactored:
    def test_delegates_to_helpers(self, provider) -> None:
        fake_resp = FakeNonStreamResponse(
            choices=[FakeNonStreamChoice(message=FakeNonStreamMessage(content="ok"))],
            usage=FakeUsage(prompt_tokens=5, completion_tokens=2),
        )
        provider._client.chat.completions.create.return_value = fake_resp
        result = provider.complete([Message.user("test")])
        assert result.content == "ok"
        provider._client.chat.completions.create.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: stream()
# ---------------------------------------------------------------------------

class TestOpenAIStream:
    def test_text_streaming(self, provider) -> None:
        chunks = [
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(content="Hello "))]),
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(content="world"))]),
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(), finish_reason="stop")]),
            FakeChunk(choices=[], usage=FakeUsage(prompt_tokens=10, completion_tokens=5)),
        ]
        provider._client.chat.completions.create.return_value = iter(chunks)

        events = list(provider.stream([Message.user("hi")]))
        types = [e.type for e in events]

        assert types.count("text_delta") == 2
        assert types[-1] == "done"
        assert events[-1].usage == {"input_tokens": 10, "output_tokens": 5}
        # Verify text content
        text_events = [e for e in events if e.type == "text_delta"]
        assert text_events[0].content == "Hello "
        assert text_events[1].content == "world"

    def test_single_tool_call_streaming(self, provider) -> None:
        chunks = [
            # Tool call start
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(
                tool_calls=[FakeToolCallDelta(
                    index=0, id="call_1",
                    function=FakeFunction(name="bash", arguments=""),
                )]
            ))]),
            # Argument deltas
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(
                tool_calls=[FakeToolCallDelta(
                    index=0,
                    function=FakeFunction(arguments='{"command"'),
                )]
            ))]),
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(
                tool_calls=[FakeToolCallDelta(
                    index=0,
                    function=FakeFunction(arguments=': "ls"}'),
                )]
            ))]),
            # Finish
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(), finish_reason="tool_calls")]),
            FakeChunk(choices=[], usage=FakeUsage(prompt_tokens=20, completion_tokens=10)),
        ]
        provider._client.chat.completions.create.return_value = iter(chunks)

        events = list(provider.stream([Message.user("list files")]))
        types = [e.type for e in events]

        assert "tool_call_start" in types
        assert "tool_call_delta" in types
        assert "tool_call_complete" in types
        assert types[-1] == "done"

        complete_event = [e for e in events if e.type == "tool_call_complete"][0]
        assert complete_event.tool_call is not None
        assert complete_event.tool_call.name == "bash"
        assert complete_event.tool_call.arguments == {"command": "ls"}

    def test_parallel_tool_calls(self, provider) -> None:
        """OpenAI sends parallel tool calls with different indices."""
        chunks = [
            # First tool call start
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(
                tool_calls=[FakeToolCallDelta(
                    index=0, id="call_1",
                    function=FakeFunction(name="bash", arguments='{"command": "ls"}'),
                )]
            ))]),
            # Second tool call start
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(
                tool_calls=[FakeToolCallDelta(
                    index=1, id="call_2",
                    function=FakeFunction(name="read_file", arguments='{"path": "x.py"}'),
                )]
            ))]),
            # Finish
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(), finish_reason="tool_calls")]),
            FakeChunk(choices=[], usage=FakeUsage(prompt_tokens=30, completion_tokens=15)),
        ]
        provider._client.chat.completions.create.return_value = iter(chunks)

        events = list(provider.stream([Message.user("do stuff")]))
        starts = [e for e in events if e.type == "tool_call_start"]
        completes = [e for e in events if e.type == "tool_call_complete"]

        assert len(starts) == 2
        assert len(completes) == 2
        assert completes[0].tool_call.name == "bash"
        assert completes[1].tool_call.name == "read_file"

    def test_usage_from_final_chunk(self, provider) -> None:
        chunks = [
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(content="hi"), finish_reason="stop")]),
            FakeChunk(choices=[], usage=FakeUsage(prompt_tokens=100, completion_tokens=50)),
        ]
        provider._client.chat.completions.create.return_value = iter(chunks)

        events = list(provider.stream([Message.user("test")]))
        done = events[-1]
        assert done.type == "done"
        assert done.usage == {"input_tokens": 100, "output_tokens": 50}


# ---------------------------------------------------------------------------
# Async stream helper
# ---------------------------------------------------------------------------

class FakeAsyncChunkStream:
    """Async iterator over chunks."""

    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


# ---------------------------------------------------------------------------
# Fixture: async provider
# ---------------------------------------------------------------------------

@pytest.fixture()
def async_provider():
    with patch.dict("sys.modules", {"openai": MagicMock()}):
        from chimera.providers.openai import OpenAIProvider
        p = OpenAIProvider.__new__(OpenAIProvider)
        p._model = "gpt-4o"
        p._client = MagicMock()
        p._async_client = MagicMock()
        p._async_client.chat.completions.create = AsyncMock()
        return p


# ---------------------------------------------------------------------------
# Tests: async_complete
# ---------------------------------------------------------------------------

class TestOpenAIAsyncComplete:
    @pytest.mark.asyncio
    async def test_native_async_complete(self, async_provider) -> None:
        fake_resp = FakeNonStreamResponse(
            choices=[FakeNonStreamChoice(message=FakeNonStreamMessage(content="async ok"))],
            usage=FakeUsage(prompt_tokens=10, completion_tokens=5),
        )
        async_provider._async_client.chat.completions.create.return_value = fake_resp
        result = await async_provider.async_complete([Message.user("hi")])
        assert result.content == "async ok"
        assert result.usage == {"input_tokens": 10, "output_tokens": 5}


# ---------------------------------------------------------------------------
# Tests: async_stream
# ---------------------------------------------------------------------------

class TestOpenAIAsyncStream:
    @pytest.mark.asyncio
    async def test_native_async_stream_text(self, async_provider) -> None:
        chunks = [
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(content="async "))]),
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(content="world"))]),
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(), finish_reason="stop")]),
            FakeChunk(choices=[], usage=FakeUsage(prompt_tokens=15, completion_tokens=8)),
        ]
        async_provider._async_client.chat.completions.create.return_value = FakeAsyncChunkStream(chunks)

        events: list = []
        async for event in async_provider.async_stream([Message.user("hi")]):
            events.append(event)

        types = [e.type for e in events]
        assert "text_delta" in types
        assert types[-1] == "done"
        assert events[-1].usage == {"input_tokens": 15, "output_tokens": 8}

    @pytest.mark.asyncio
    async def test_native_async_stream_tool_use(self, async_provider) -> None:
        chunks = [
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(
                tool_calls=[FakeToolCallDelta(
                    index=0, id="call_1",
                    function=FakeFunction(name="bash", arguments='{"command": "ls"}'),
                )]
            ))]),
            FakeChunk(choices=[FakeChoice(delta=FakeDelta(), finish_reason="tool_calls")]),
            FakeChunk(choices=[], usage=FakeUsage(prompt_tokens=20, completion_tokens=10)),
        ]
        async_provider._async_client.chat.completions.create.return_value = FakeAsyncChunkStream(chunks)

        events: list = []
        async for event in async_provider.async_stream([Message.user("run ls")]):
            events.append(event)

        types = [e.type for e in events]
        assert "tool_call_start" in types
        assert "tool_call_complete" in types
        assert types[-1] == "done"

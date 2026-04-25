"""Tests for Provider async_complete() and async_stream() defaults."""
from __future__ import annotations

import pytest

from chimera.providers.base import Provider, Response, StreamEvent, ToolSchema
from chimera.types import Message, ToolCall


class SyncOnlyProvider(Provider):
    """Provider that only implements sync methods."""

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
        return "sync-test"


class TestAsyncComplete:
    @pytest.mark.asyncio
    async def test_wraps_sync_complete(self) -> None:
        response = Response(
            content="Hello",
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        provider = SyncOnlyProvider(response)
        result = await provider.async_complete([Message.user("hi")])
        assert result.content == "Hello"
        assert result.usage == {"input_tokens": 10, "output_tokens": 5}

    @pytest.mark.asyncio
    async def test_with_tool_calls(self) -> None:
        tc = ToolCall(id="c1", name="bash", arguments={"command": "ls"})
        response = Response(
            content="",
            tool_calls=[tc],
            usage={"input_tokens": 20, "output_tokens": 10},
        )
        provider = SyncOnlyProvider(response)
        result = await provider.async_complete([Message.user("list")])
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "bash"


class TestAsyncStream:
    @pytest.mark.asyncio
    async def test_bridges_sync_stream(self) -> None:
        response = Response(
            content="Hello world",
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        provider = SyncOnlyProvider(response)

        events: list[StreamEvent] = []
        async for event in provider.async_stream([Message.user("hi")]):
            events.append(event)

        types = [e.type for e in events]
        assert "text_delta" in types
        assert types[-1] == "done"
        assert events[-1].usage == {"input_tokens": 10, "output_tokens": 5}

    @pytest.mark.asyncio
    async def test_tool_calls_streamed(self) -> None:
        tc = ToolCall(id="c1", name="bash", arguments={"command": "ls"})
        response = Response(
            content="",
            tool_calls=[tc],
            usage={"input_tokens": 20, "output_tokens": 10},
        )
        provider = SyncOnlyProvider(response)

        events: list[StreamEvent] = []
        async for event in provider.async_stream([Message.user("list")]):
            events.append(event)

        types = [e.type for e in events]
        assert "tool_call_start" in types
        assert types[-1] == "done"

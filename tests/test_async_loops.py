"""Tests for ReAct.async_run(), StreamingReAct.async_run(), and Agent.async_run()."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from chimera.core.agent import Agent
from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider, Response, StreamEvent, ToolSchema
from chimera.types import AgentResult, Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class AsyncMockProvider(Provider):
    """Provider with mock async_complete."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None) -> Response:
        resp = self._responses[self._idx]
        self._idx += 1
        return resp

    async def async_complete(self, messages, tools=None, temperature=0.0, max_tokens=None) -> Response:
        resp = self._responses[self._idx]
        self._idx += 1
        return resp

    @property
    def context_window(self) -> int:
        return 4096

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "async-mock"


class EchoTool(BaseTool):
    name = "echo"
    description = "Echoes input"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return ToolResult(output=f"echo: {args}")


def text_response(content: str) -> Response:
    return Response(content=content, tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5})


def tool_response(content: str, tool_name: str = "echo", call_id: str = "c1") -> Response:
    return Response(
        content=content,
        tool_calls=[ToolCall(id=call_id, name=tool_name, arguments={"msg": "hi"})],
        usage={"input_tokens": 10, "output_tokens": 5},
    )


# ---------------------------------------------------------------------------
# Tests: ReAct.async_run
# ---------------------------------------------------------------------------

class TestReActAsyncRun:
    @pytest.mark.asyncio
    async def test_text_only(self) -> None:
        provider = AsyncMockProvider([text_response("Hello")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))
        result = await loop.async_run(provider, [], context, None)
        assert result.success is True
        assert result.output == "Hello"

    @pytest.mark.asyncio
    async def test_tool_then_text(self) -> None:
        provider = AsyncMockProvider([tool_response("I'll echo"), text_response("Done")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("echo"))
        result = await loop.async_run(provider, [EchoTool()], context, None)
        assert result.success is True
        assert result.steps == 2

    @pytest.mark.asyncio
    async def test_max_steps(self) -> None:
        responses = [tool_response("step", call_id=f"c{i}") for i in range(5)]
        provider = AsyncMockProvider(responses)
        loop = ReAct(max_steps=3)
        context = Context(system="test")
        context.add(Message.user("go"))
        result = await loop.async_run(provider, [EchoTool()], context, None)
        assert result.success is False
        assert result.error == "Max steps reached"


# ---------------------------------------------------------------------------
# Tests: Agent.async_run
# ---------------------------------------------------------------------------

class TestAgentAsyncRun:
    @pytest.mark.asyncio
    async def test_end_to_end(self) -> None:
        provider = AsyncMockProvider([text_response("Agent says hello")])
        agent = Agent(provider=provider, tools=[])
        result = await agent.async_run("say hi", None)
        assert result.success is True
        assert result.output == "Agent says hello"

    @pytest.mark.asyncio
    async def test_with_tool(self) -> None:
        provider = AsyncMockProvider([tool_response("I'll echo"), text_response("Done")])
        agent = Agent(provider=provider, tools=[EchoTool()])
        result = await agent.async_run("echo something", None)
        assert result.success is True
        assert result.steps == 2

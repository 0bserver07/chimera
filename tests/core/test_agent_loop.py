"""Tests for chimera.core.agent_loop — AgentLoop with AsyncGenerator protocol."""
from __future__ import annotations

import pytest

from chimera.core.agent_loop import AgentLoop
from chimera.core.loop_events import LoopEventType
from chimera.core.tool import BaseTool
from chimera.providers.base import Response
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockProvider:
    """Minimal mock provider that yields canned responses in order."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = iter(responses)
        self.model_name = "mock"

    async def async_complete(self, messages, tools=None, **kwargs):
        return next(self._responses)


class EchoTool(BaseTool):
    """Simple tool that echoes its ``text`` argument."""

    name = "echo"
    description = "echoes input"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    is_concurrency_safe = True

    def execute(self, args, env):
        return ToolResult(output=args.get("text", ""))

    async def async_execute(self, args, env):
        return ToolResult(output=args.get("text", ""))


# ---------------------------------------------------------------------------
# Test 1: Simple completion (no tools)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_completion_no_tools():
    provider = MockProvider([Response(content="Hello!", tool_calls=[], usage={})])
    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("Hi")],
        tools=[],
        provider=provider,
        system_prompt="You are helpful.",
    ):
        events.append(event)

    assert any(e.type == LoopEventType.result for e in events)
    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "completed"


# ---------------------------------------------------------------------------
# Test 2: Tool call then completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_then_completion():
    responses = [
        Response(
            content="Let me echo",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hello"})],
            usage={},
        ),
        Response(content="Done!", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("Echo hello")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="You are helpful.",
    ):
        events.append(event)

    tool_results = [e for e in events if e.type == LoopEventType.tool_result]
    assert len(tool_results) >= 1
    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "completed"
    assert result_event.data.turn_count == 2


# ---------------------------------------------------------------------------
# Test 3: Max turns exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_turns_exit():
    def make_response():
        return Response(
            content="again",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "x"})],
            usage={},
        )

    provider = MockProvider([make_response() for _ in range(10)])
    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("loop forever")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
        max_turns=3,
    ):
        events.append(event)

    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "max_turns"

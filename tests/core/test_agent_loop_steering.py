"""Tests for steering/follow-up message injection in AgentLoop."""
from __future__ import annotations


import pytest

from chimera.core.agent_loop import AgentLoop
from chimera.core.loop_events import LoopEventType
from chimera.core.message_queue import SteeringMessageQueue
from chimera.core.tool import BaseTool
from chimera.providers.base import Response
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockProvider:
    """Provider that yields canned responses in order."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = iter(responses)
        self.model_name = "mock"
        self.calls: list[list[Message]] = []

    async def async_complete(self, messages, tools=None, **kwargs):
        self.calls.append(list(messages))
        return next(self._responses)


class EchoTool(BaseTool):
    name = "echo"
    description = "echoes input"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    is_concurrency_safe = True

    def execute(self, args, env):
        return ToolResult(output=args.get("text", ""))

    async def async_execute(self, args, env):
        return ToolResult(output=args.get("text", ""))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steering_messages_injected_after_tools():
    """Steering messages should be injected into working_messages after tool execution."""
    mq = SteeringMessageQueue()

    # Phase 1: model calls echo tool
    # Phase 2: after tool exec, steering msg is drained and appended
    # Phase 3: model sees the steering msg and completes
    responses = [
        Response(
            content="calling echo",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
            usage={},
        ),
        Response(content="Done with steering!", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)

    # Add steering message before run - it will be drained after tool execution
    mq.add_steering(Message.user("Please also summarize"))

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("Start task")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
        message_queue=mq,
    ):
        events.append(event)

    result = next(e for e in events if e.type == LoopEventType.result)
    assert result.data.reason == "completed"

    # The second model call should include the steering message in its context
    assert len(provider.calls) == 2
    second_call_messages = provider.calls[1]
    contents = [m.content for m in second_call_messages]
    assert "Please also summarize" in contents


@pytest.mark.asyncio
async def test_follow_up_prevents_early_stop():
    """Follow-up messages should prevent the loop from returning 'completed' and continue."""
    mq = SteeringMessageQueue()

    # Model will try to stop (no tool calls) on first response,
    # but follow-up message should inject and continue.
    # On second response, it stops for real.
    responses = [
        Response(content="I'm done!", tool_calls=[], usage={}),
        Response(content="OK, done with follow-up too.", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)

    # Add follow-up message before run
    mq.add_follow_up(Message.user("Actually, one more thing"))

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("Do something")],
        tools=[],
        provider=provider,
        system_prompt="test",
        message_queue=mq,
    ):
        events.append(event)

    result = next(e for e in events if e.type == LoopEventType.result)
    assert result.data.reason == "completed"

    # Provider should have been called twice: once for original, once after follow-up
    assert len(provider.calls) == 2

    # The second call should contain the follow-up message
    second_call_messages = provider.calls[1]
    contents = [m.content for m in second_call_messages]
    assert "Actually, one more thing" in contents

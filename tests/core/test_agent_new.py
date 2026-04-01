"""Tests for Agent.async_run_events() — new infrastructure wiring (CG-5)."""
from __future__ import annotations

import pytest

from chimera.core.agent import Agent
from chimera.core.loop_events import LoopEventType
from chimera.providers.base import Response
from chimera.types import AgentResult, Message, ToolCall


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockProvider:
    """Minimal mock provider that yields canned responses."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = iter(responses)
        self.model_name = "mock"
        self.context_window_size = 128_000
        self.supports_tools = True

    @property
    def context_window(self) -> int:
        return self.context_window_size

    @property
    def supports_tool_use(self) -> bool:
        return self.supports_tools

    def complete(self, messages, tools=None, **kwargs):
        return next(self._responses)

    async def async_complete(self, messages, tools=None, **kwargs):
        return next(self._responses)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_async_run_events():
    """Agent.async_run_events() yields LoopEvents using new infrastructure."""
    provider = MockProvider([Response(content="Done!", tool_calls=[], usage={})])
    agent = Agent(provider=provider, tools=[])
    events = []
    async for event in agent.async_run_events("Hello"):
        events.append(event)
    result = next(e for e in events if e.type == LoopEventType.result)
    assert result.data.reason == "completed"


@pytest.mark.asyncio
async def test_agent_async_run_events_yields_stream_start():
    """async_run_events should yield a stream_start event before assistant."""
    provider = MockProvider([Response(content="Hi!", tool_calls=[], usage={})])
    agent = Agent(provider=provider, tools=[])
    event_types = []
    async for event in agent.async_run_events("Hello"):
        event_types.append(event.type)
    assert LoopEventType.stream_start in event_types
    assert LoopEventType.assistant in event_types
    # stream_start should come before assistant
    assert event_types.index(LoopEventType.stream_start) < event_types.index(LoopEventType.assistant)


@pytest.mark.asyncio
async def test_agent_async_run_events_multiple_turns():
    """async_run_events handles tool-call turns correctly."""
    from chimera.core.tool import BaseTool
    from chimera.types import ToolResult

    class SimpleTool(BaseTool):
        name = "greet"
        description = "greets"
        parameters = {"type": "object", "properties": {"name": {"type": "string"}}}
        is_concurrency_safe = True

        def execute(self, args, env):
            return ToolResult(output=f"Hi {args.get('name', 'world')}!")

        async def async_execute(self, args, env):
            return ToolResult(output=f"Hi {args.get('name', 'world')}!")

    responses = [
        Response(
            content="Let me greet",
            tool_calls=[ToolCall(id="t1", name="greet", arguments={"name": "Alice"})],
            usage={},
        ),
        Response(content="Greeted Alice!", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    agent = Agent(provider=provider, tools=[SimpleTool()])
    events = []
    async for event in agent.async_run_events("Greet Alice"):
        events.append(event)

    result = next(e for e in events if e.type == LoopEventType.result)
    assert result.data.reason == "completed"
    assert result.data.turn_count == 2

    tool_results = [e for e in events if e.type == LoopEventType.tool_result]
    assert len(tool_results) == 1


def test_agent_run_still_works():
    """Old Agent.run() still works (backwards compatible)."""
    provider = MockProvider([Response(content="Hi!", tool_calls=[], usage={})])
    agent = Agent(provider=provider)
    result = agent.run("Hello", env=None)
    assert isinstance(result, AgentResult)


def test_agent_iter_steps_still_works():
    """Old Agent.iter_steps() still works (backwards compatible)."""
    provider = MockProvider([Response(content="Hi!", tool_calls=[], usage={})])
    agent = Agent(provider=provider)
    gen = agent.iter_steps("Hello", env=None)
    # Consume the generator
    steps = []
    try:
        while True:
            step = next(gen)
            steps.append(step)
    except StopIteration as e:
        result = e.value
    assert isinstance(result, AgentResult)

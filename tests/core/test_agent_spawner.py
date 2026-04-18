"""Tests for chimera.core.agent_spawner — AgentSpawner for sub-agent creation."""
from __future__ import annotations

import asyncio

import pytest

from chimera.core.abort import AbortSignal
from chimera.core.agent_context import AgentContext
from chimera.core.agent_definition import AgentDefinition
from chimera.core.agent_spawner import AgentSpawner
from chimera.core.loop_events import LoopEventType
from chimera.core.loop_state import QuerySource
from chimera.core.task_manager import TaskManager
from chimera.core.tool import BaseTool
from chimera.providers.base import Response
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class MockProvider:
    """Minimal mock provider that yields canned responses."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = iter(responses)
        self.model_name = "mock"

    async def async_complete(self, messages, tools=None, **kwargs):
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


def _make_parent_context() -> AgentContext:
    return AgentContext(
        messages=[],
        file_state_cache={},
        abort_signal=AbortSignal(),
        denial_tracking={},
        agent_id="parent-1",
        parent_agent_id=None,
        query_source=QuerySource.FOREGROUND,
        depth=0,
        get_app_state=lambda: {},
        set_app_state=lambda u: None,
        set_app_state_for_tasks=lambda u: None,
    )


# ---------------------------------------------------------------------------
# Test 1: Spawner produces events from AgentLoop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_produces_events():
    definition = AgentDefinition(
        name="test-agent",
        description="Test",
        system_prompt="You are helpful.",
    )
    provider = MockProvider([
        Response(content="Hello from sub-agent!", tool_calls=[], usage={}),
    ])

    spawner = AgentSpawner(
        provider=provider,
        available_tools=[],
        task_manager=TaskManager(),
    )

    parent_ctx = _make_parent_context()
    events = []
    async for event in spawner.spawn(
        definition=definition,
        prompt="Say hello",
        parent_context=parent_ctx,
    ):
        events.append(event)

    # Should have at least a stream_start, assistant, and result event
    event_types = [e.type for e in events]
    assert LoopEventType.stream_start in event_types
    assert LoopEventType.assistant in event_types
    assert LoopEventType.result in event_types

    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "completed"


# ---------------------------------------------------------------------------
# Test 2: Spawner creates child context with correct isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_creates_child_context():
    definition = AgentDefinition(
        name="child-agent",
        description="Child",
        system_prompt="You are a child agent.",
    )
    provider = MockProvider([
        Response(content="Done", tool_calls=[], usage={}),
    ])

    parent_ctx = _make_parent_context()
    parent_ctx.messages.append(Message.user("parent message"))

    spawner = AgentSpawner(
        provider=provider,
        available_tools=[],
        task_manager=TaskManager(),
    )

    events = []
    async for event in spawner.spawn(
        definition=definition,
        prompt="Do something",
        parent_context=parent_ctx,
    ):
        events.append(event)

    # Parent messages should not be modified by child spawn
    assert len(parent_ctx.messages) == 1


# ---------------------------------------------------------------------------
# Test 3: Spawner uses definition's system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_uses_definition_system_prompt():
    """Verify the spawner passes the definition system_prompt to AgentLoop."""
    received_messages = []

    class CapturingProvider:
        model_name = "capture"

        async def async_complete(self, messages, tools=None, **kwargs):
            received_messages.extend(messages)
            return Response(content="ok", tool_calls=[], usage={})

    definition = AgentDefinition(
        name="prompt-test",
        description="Test prompt",
        system_prompt="CUSTOM SYSTEM PROMPT HERE",
    )

    spawner = AgentSpawner(
        provider=CapturingProvider(),
        available_tools=[],
        task_manager=TaskManager(),
    )

    parent_ctx = _make_parent_context()
    async for _ in spawner.spawn(
        definition=definition,
        prompt="test",
        parent_context=parent_ctx,
    ):
        pass

    # The system prompt should be the first message
    system_msgs = [m for m in received_messages if m.role == "system"]
    assert any("CUSTOM SYSTEM PROMPT HERE" in m.content for m in system_msgs)


# ---------------------------------------------------------------------------
# Test 4: Spawner filters tools based on definition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_filters_tools():
    """When a definition specifies tool names, only those tools are passed."""
    received_tools = []

    class CapturingProvider:
        model_name = "capture"

        async def async_complete(self, messages, tools=None, **kwargs):
            received_tools.append(tools)
            return Response(content="ok", tool_calls=[], usage={})

    definition = AgentDefinition(
        name="filtered",
        description="Test",
        tools=["echo"],
        system_prompt="test",
    )

    class OtherTool(BaseTool):
        name = "other"
        description = "other tool"
        parameters = {"type": "object", "properties": {}}

        def execute(self, args, env):
            return ToolResult(output="other")

    spawner = AgentSpawner(
        provider=CapturingProvider(),
        available_tools=[EchoTool(), OtherTool()],
        task_manager=TaskManager(),
    )

    parent_ctx = _make_parent_context()
    async for _ in spawner.spawn(
        definition=definition,
        prompt="test",
        parent_context=parent_ctx,
    ):
        pass

    # Only "echo" should be in the tool schemas, not "other"
    assert len(received_tools) == 1
    tool_schemas = received_tools[0]
    assert len(tool_schemas) == 1
    assert tool_schemas[0]["name"] == "echo"


# ---------------------------------------------------------------------------
# Test 5: Spawner with tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_with_tool_calls():
    definition = AgentDefinition(
        name="tool-user",
        description="Uses tools",
        system_prompt="test",
    )
    provider = MockProvider([
        Response(
            content="Let me echo",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
            usage={},
        ),
        Response(content="Done!", tool_calls=[], usage={}),
    ])

    spawner = AgentSpawner(
        provider=provider,
        available_tools=[EchoTool()],
        task_manager=TaskManager(),
    )

    parent_ctx = _make_parent_context()
    events = []
    async for event in spawner.spawn(
        definition=definition,
        prompt="Echo hi",
        parent_context=parent_ctx,
    ):
        events.append(event)

    tool_results = [e for e in events if e.type == LoopEventType.tool_result]
    assert len(tool_results) >= 1
    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "completed"
    assert result_event.data.turn_count == 2


# ---------------------------------------------------------------------------
# Test 6: Background spawn yields single event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_background():
    definition = AgentDefinition(
        name="bg-agent",
        description="Background agent",
        system_prompt="test",
    )
    provider = MockProvider([
        Response(content="background done", tool_calls=[], usage={}),
    ])

    task_manager = TaskManager()
    spawner = AgentSpawner(
        provider=provider,
        available_tools=[],
        task_manager=task_manager,
    )

    parent_ctx = _make_parent_context()
    events = []
    async for event in spawner.spawn(
        definition=definition,
        prompt="Run in background",
        parent_context=parent_ctx,
        run_in_background=True,
    ):
        events.append(event)

    # Should yield exactly one async_launched event
    assert len(events) == 1
    assert events[0].type == LoopEventType.system
    assert "background" in str(events[0].data).lower() or "launched" in str(events[0].data).lower()

    # A background task should have been registered
    tasks = task_manager.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].agent_id is not None

    # Wait briefly for the background task to complete
    await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# Test 7: Spawner respects abort on parent context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_respects_abort():
    definition = AgentDefinition(
        name="abortable",
        description="Test",
        system_prompt="test",
    )

    parent_ctx = _make_parent_context()
    parent_ctx.abort_signal.abort("test abort")

    provider = MockProvider([
        Response(content="should not reach", tool_calls=[], usage={}),
    ])

    spawner = AgentSpawner(
        provider=provider,
        available_tools=[],
        task_manager=TaskManager(),
    )

    events = []
    async for event in spawner.spawn(
        definition=definition,
        prompt="test",
        parent_context=parent_ctx,
        share_abort=True,
    ):
        events.append(event)

    # Since parent was aborted, the child should abort quickly
    result_events = [e for e in events if e.type == LoopEventType.result]
    assert len(result_events) == 1
    assert "abort" in result_events[0].data.reason


# ---------------------------------------------------------------------------
# Test 8: Definition tools=None passes all available tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_all_tools_when_none():
    received_tools = []

    class CapturingProvider:
        model_name = "capture"

        async def async_complete(self, messages, tools=None, **kwargs):
            received_tools.append(tools)
            return Response(content="ok", tool_calls=[], usage={})

    definition = AgentDefinition(
        name="all-tools",
        description="Test",
        tools=None,  # All tools
        system_prompt="test",
    )

    spawner = AgentSpawner(
        provider=CapturingProvider(),
        available_tools=[EchoTool()],
        task_manager=TaskManager(),
    )

    parent_ctx = _make_parent_context()
    async for _ in spawner.spawn(
        definition=definition,
        prompt="test",
        parent_context=parent_ctx,
    ):
        pass

    assert len(received_tools) == 1
    tool_schemas = received_tools[0]
    assert len(tool_schemas) == 1
    assert tool_schemas[0]["name"] == "echo"

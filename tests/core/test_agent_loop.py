"""Tests for chimera.core.agent_loop — AgentLoop with AsyncGenerator protocol."""
from __future__ import annotations

import asyncio

import pytest

from chimera.core.agent_loop import AgentLoop
from chimera.core.loop_events import LoopEventType
from chimera.core.system_prompt import SystemPrompt, PromptLayer
from chimera.core.tool import BaseTool
from chimera.permissions.checker import PermissionChecker
from chimera.permissions.context import PermissionContext
from chimera.permissions.decisions import PermissionDecision
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import PermissionBehavior, RuleSource
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


# ---------------------------------------------------------------------------
# Test 4: LoopState turn_count increments (CG-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uses_loop_state():
    """Verify that LoopState is used and turn_count increments correctly."""
    responses = [
        Response(
            content="tool time",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "a"})],
            usage={},
        ),
        Response(
            content="tool time 2",
            tool_calls=[ToolCall(id="t2", name="echo", arguments={"text": "b"})],
            usage={},
        ),
        Response(content="Done!", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("go")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
    ):
        events.append(event)

    result_event = next(e for e in events if e.type == LoopEventType.result)
    # 2 tool-call turns + 1 completion turn = 3
    assert result_event.data.turn_count == 3
    assert result_event.data.reason == "completed"


# ---------------------------------------------------------------------------
# Test 5: ErrorRecovery on max_output_tokens (CG-1)
# ---------------------------------------------------------------------------


class MaxTokensProvider:
    """Provider that raises max_output_tokens on the first call, then succeeds."""

    def __init__(self) -> None:
        self.call_count = 0
        self.model_name = "mock"

    async def async_complete(self, messages, tools=None, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            raise RuntimeError("max_output_tokens exceeded")
        return Response(content="Recovered!", tool_calls=[], usage={})


@pytest.mark.asyncio
async def test_error_recovery_on_max_output_tokens():
    """Provider raises max_output_tokens; ErrorRecovery should retry."""
    provider = MaxTokensProvider()
    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("big output")],
        tools=[],
        provider=provider,
        system_prompt="test",
    ):
        events.append(event)

    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "completed"
    # Provider was called twice: first failed, then succeeded after recovery
    assert provider.call_count == 2


# ---------------------------------------------------------------------------
# Test 6: Permission check blocks tool (CG-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permission_check_blocks_tool():
    """PermissionChecker DENY -> tool skipped with error message."""
    responses = [
        Response(
            content="Let me echo",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "secret"})],
            usage={},
        ),
        Response(content="OK, skipped.", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)

    # Build a PermissionContext that denies 'echo'
    perm_ctx = PermissionContext(
        mode=PermissionMode.DEFAULT,
        deny_rules={RuleSource.SESSION: ["echo"]},
    )
    checker = PermissionChecker()

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("echo secret")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
        permission_checker=checker,
        permission_context=perm_ctx,
    ):
        events.append(event)

    tool_result_events = [e for e in events if e.type == LoopEventType.tool_result]
    assert len(tool_result_events) >= 1
    tc, result = tool_result_events[0].data
    assert result.error is not None
    assert "denied" in result.error.lower() or "permission" in result.error.lower()


# ---------------------------------------------------------------------------
# Test 7: Concurrent tool execution (CG-8)
# ---------------------------------------------------------------------------


class ConcurrentTool(BaseTool):
    """A concurrency-safe tool that records its invocation order."""

    name = "concurrent"
    description = "concurrent test tool"
    parameters = {"type": "object", "properties": {"id": {"type": "string"}}}
    is_concurrency_safe = True

    execution_log: list[str] = []

    def execute(self, args, env):
        return ToolResult(output=args.get("id", ""))

    async def async_execute(self, args, env):
        tool_id = args.get("id", "")
        ConcurrentTool.execution_log.append(tool_id)
        return ToolResult(output=tool_id)


@pytest.mark.asyncio
async def test_concurrent_tool_execution():
    """Submit 3 concurrent-safe tools via a single executor, verify all run."""
    ConcurrentTool.execution_log = []

    responses = [
        Response(
            content="Running 3 tools",
            tool_calls=[
                ToolCall(id="c1", name="concurrent", arguments={"id": "A"}),
                ToolCall(id="c2", name="concurrent", arguments={"id": "B"}),
                ToolCall(id="c3", name="concurrent", arguments={"id": "C"}),
            ],
            usage={},
        ),
        Response(content="All done!", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("run 3")],
        tools=[ConcurrentTool()],
        provider=provider,
        system_prompt="test",
    ):
        events.append(event)

    tool_result_events = [e for e in events if e.type == LoopEventType.tool_result]
    assert len(tool_result_events) == 3

    # Verify all 3 produced results
    result_ids = {e.data[1].output for e in tool_result_events}
    assert result_ids == {"A", "B", "C"}

    # Verify all 3 were actually executed
    assert set(ConcurrentTool.execution_log) == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# Test 8: SystemPrompt object accepted (CG-3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_prompt_object_accepted():
    """SystemPrompt object should work the same as a plain string."""
    sp = SystemPrompt(layers=[
        PromptLayer(name="base", content="You are helpful."),
        PromptLayer(name="extra", content="Be concise."),
    ])

    captured_messages = []

    class CapturingProvider:
        model_name = "mock"

        async def async_complete(self, messages, tools=None, **kwargs):
            captured_messages.extend(messages)
            return Response(content="OK!", tool_calls=[], usage={})

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("Hi")],
        tools=[],
        provider=CapturingProvider(),
        system_prompt=sp,
    ):
        events.append(event)

    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "completed"

    # Verify the system prompt was converted to string via to_string()
    system_msgs = [m for m in captured_messages if m.role == "system"]
    assert len(system_msgs) == 1
    assert "You are helpful." in system_msgs[0].content
    assert "Be concise." in system_msgs[0].content

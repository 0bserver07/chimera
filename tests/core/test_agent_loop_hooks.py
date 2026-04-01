"""Tests for hook integration in AgentLoop."""
from __future__ import annotations

import pytest

from chimera.core.agent_loop import AgentLoop
from chimera.core.loop_events import LoopEventType
from chimera.core.tool import BaseTool
from chimera.hooks.events import HookEvent
from chimera.hooks.executor import HookExecutor
from chimera.hooks.types import FunctionHook, HookInput, HookMatcher, HookOutput
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
    name = "echo"
    description = "echoes input"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    is_concurrency_safe = True

    def execute(self, args, env):
        return ToolResult(output=args.get("text", ""))

    async def async_execute(self, args, env):
        return ToolResult(output=args.get("text", ""))


# ---------------------------------------------------------------------------
# Test: PRE_TOOL_USE hook can block a tool call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_tool_use_hook_blocks_tool():
    """When a PRE_TOOL_USE hook returns continue_execution=False,
    the tool call should be skipped and the model should get a
    denial message instead of the tool result."""
    def block_echo(input_data):
        return HookOutput(
            continue_execution=False,
            reason="echo tool is blocked by policy",
        )

    hook = FunctionHook(callback=block_echo)
    matcher = HookMatcher(hooks=[hook], matcher="echo")

    # Provider: first call returns a tool call, second returns completion.
    # If the hook blocks the tool, the loop will feed back a denial
    # message and the provider will be called again.
    responses = [
        Response(
            content="Let me echo",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hello"})],
            usage={},
        ),
        Response(content="OK, tool was blocked", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    executor = HookExecutor()

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("echo hello")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
        hook_executor=executor,
        hook_matchers=[matcher],
    ):
        events.append(event)

    # The tool_result should contain the denial, not the actual echo output.
    tool_results = [e for e in events if e.type == LoopEventType.tool_result]
    assert len(tool_results) >= 1
    # The denial reason should appear in the tool result data
    tc, result = tool_results[0].data
    assert "blocked" in result.output.lower() or "blocked" in (result.error or "").lower()

    # The loop should still complete normally
    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "completed"


# ---------------------------------------------------------------------------
# Test: PRE_TOOL_USE hook with updated_input modifies tool args
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_tool_use_hook_updates_input():
    """When a PRE_TOOL_USE hook returns updated_input, the tool should
    receive the modified arguments."""
    def modify_input(input_data):
        return HookOutput(
            continue_execution=True,
            updated_input={"text": "modified"},
        )

    hook = FunctionHook(callback=modify_input)
    matcher = HookMatcher(hooks=[hook], matcher="echo")

    responses = [
        Response(
            content="echoing",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "original"})],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    executor = HookExecutor()

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("echo something")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
        hook_executor=executor,
        hook_matchers=[matcher],
    ):
        events.append(event)

    # The tool should have received "modified" instead of "original"
    tool_results = [e for e in events if e.type == LoopEventType.tool_result]
    assert len(tool_results) >= 1
    tc, result = tool_results[0].data
    assert result.output == "modified"


# ---------------------------------------------------------------------------
# Test: POST_TOOL_USE hook fires after tool execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_tool_use_hook_fires():
    """POST_TOOL_USE hooks fire after tool execution."""
    post_hook_calls = []

    def track_post(input_data):
        post_hook_calls.append(input_data.tool_name)
        return HookOutput()

    hook = FunctionHook(callback=track_post)
    matcher = HookMatcher(hooks=[hook])

    responses = [
        Response(
            content="echoing",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    executor = HookExecutor()

    loop = AgentLoop()
    async for _ in loop.run(
        messages=[Message.user("echo")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
        hook_executor=executor,
        hook_matchers=[matcher],
    ):
        pass

    assert "echo" in post_hook_calls


# ---------------------------------------------------------------------------
# Test: STOP hook can prevent completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_hook_prevents_completion():
    """When a STOP hook returns continue_execution=False, the loop
    should inject a stop_reason message and continue instead of ending."""
    stop_hook_calls = []

    def block_stop(input_data):
        # Only act on STOP events; ignore SESSION_START / SESSION_END
        if input_data.event != HookEvent.STOP.value:
            return HookOutput(continue_execution=True)
        stop_hook_calls.append(True)
        if len(stop_hook_calls) == 1:
            return HookOutput(
                continue_execution=False,
                stop_reason="Not done yet — check your work",
            )
        return HookOutput(continue_execution=True)

    hook = FunctionHook(callback=block_stop)
    matcher = HookMatcher(hooks=[hook])

    responses = [
        # First: model says done (no tool calls)
        Response(content="I'm done!", tool_calls=[], usage={}),
        # Second: model called again after STOP hook rejection, says done again
        Response(content="Now I'm really done!", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    executor = HookExecutor()

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("do something")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
        hook_executor=executor,
        hook_matchers=[matcher],
    ):
        events.append(event)

    # STOP hook was called twice (once blocked, once allowed)
    assert len(stop_hook_calls) == 2

    # The loop should eventually complete
    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "completed"

    # There should be 2 assistant events (model called twice)
    assistant_events = [e for e in events if e.type == LoopEventType.assistant]
    assert len(assistant_events) == 2


# ---------------------------------------------------------------------------
# Test: No hooks = original behavior unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_hooks_unchanged_behavior():
    """When no hook_executor is provided, loop behavior is unchanged."""
    responses = [
        Response(
            content="echoing",
            tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hi"})],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("echo")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
    ):
        events.append(event)

    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "completed"


# ---------------------------------------------------------------------------
# Test: SESSION_START hook fires at the beginning of the loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_hook_fires():
    """SESSION_START hook should fire at the very beginning of run()."""
    session_start_calls = []

    def on_session_start(input_data):
        session_start_calls.append(input_data.event)
        return HookOutput()

    hook = FunctionHook(callback=on_session_start)
    matcher = HookMatcher(hooks=[hook])

    responses = [
        Response(content="done", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    executor = HookExecutor()

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("hello")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
        hook_executor=executor,
        hook_matchers=[matcher],
    ):
        events.append(event)

    # SESSION_START should have been called
    assert HookEvent.SESSION_START.value in session_start_calls

    # The loop should complete normally
    result_event = next(e for e in events if e.type == LoopEventType.result)
    assert result_event.data.reason == "completed"


# ---------------------------------------------------------------------------
# Test: SESSION_END hook fires before result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_end_hook_fires():
    """SESSION_END hook should fire right before the final RESULT event."""
    session_end_calls = []

    def on_session_end(input_data):
        session_end_calls.append(input_data.event)
        return HookOutput()

    hook = FunctionHook(callback=on_session_end)
    matcher = HookMatcher(hooks=[hook])

    responses = [
        Response(content="done", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    executor = HookExecutor()

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("hello")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
        hook_executor=executor,
        hook_matchers=[matcher],
    ):
        events.append(event)

    # SESSION_END should have been called
    assert HookEvent.SESSION_END.value in session_end_calls


# ---------------------------------------------------------------------------
# Test: POST_TOOL_USE_FAILURE fires on tool error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_tool_use_failure_fires_on_error():
    """POST_TOOL_USE_FAILURE should fire instead of POST_TOOL_USE
    when a tool result has an error."""
    hook_events_fired = []

    def track_event(input_data):
        hook_events_fired.append(input_data.event)
        return HookOutput()

    hook = FunctionHook(callback=track_event)
    matcher = HookMatcher(hooks=[hook])

    class FailingTool(BaseTool):
        name = "fail"
        description = "always fails"
        parameters = {"type": "object", "properties": {}}
        is_concurrency_safe = True

        def execute(self, args, env):
            return ToolResult(output="", error="something broke")

        async def async_execute(self, args, env):
            return ToolResult(output="", error="something broke")

    responses = [
        Response(
            content="running",
            tool_calls=[ToolCall(id="t1", name="fail", arguments={})],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    executor = HookExecutor()

    loop = AgentLoop()
    async for _ in loop.run(
        messages=[Message.user("do it")],
        tools=[FailingTool()],
        provider=provider,
        system_prompt="test",
        hook_executor=executor,
        hook_matchers=[matcher],
    ):
        pass

    # POST_TOOL_USE_FAILURE should have fired (not POST_TOOL_USE)
    assert HookEvent.POST_TOOL_USE_FAILURE.value in hook_events_fired
    assert HookEvent.POST_TOOL_USE.value not in hook_events_fired

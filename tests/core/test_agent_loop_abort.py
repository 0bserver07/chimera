import pytest
import asyncio
from chimera.core.agent_loop import AgentLoop
from chimera.core.abort import AbortSignal
from chimera.core.loop_events import LoopEventType
from chimera.types import Message, ToolCall, ToolResult
from chimera.providers.base import Response
from chimera.core.tool import BaseTool


class SlowTool(BaseTool):
    name = "slow"
    description = "a slow tool"
    parameters = {"type": "object", "properties": {}}
    is_concurrency_safe = False

    def execute(self, args, env):
        return ToolResult(output="done")

    async def async_execute(self, args, env):
        await asyncio.sleep(10)  # Very slow — should be aborted
        return ToolResult(output="done")


class SlowProvider:
    model_name = "mock"

    async def async_complete(self, messages, tools=None, **kwargs):
        return Response(
            content="calling tool",
            tool_calls=[ToolCall(id="t1", name="slow", arguments={})],
            usage={},
        )


@pytest.mark.asyncio
async def test_abort_during_tool_execution():
    abort = AbortSignal()

    loop = AgentLoop()

    # Schedule abort after 100ms
    async def schedule_abort():
        await asyncio.sleep(0.1)
        abort.abort("user cancelled")

    abort_task = asyncio.create_task(schedule_abort())

    events = []
    async for event in loop.run(
        messages=[Message.user("do something slow")],
        tools=[SlowTool()],
        provider=SlowProvider(),
        system_prompt="test",
        abort_signal=abort,
    ):
        events.append(event)

    await abort_task  # Clean up

    result_event = next(
        (e for e in events if e.type == LoopEventType.result), None
    )
    assert result_event is not None
    assert "abort" in result_event.data.reason


@pytest.mark.asyncio
async def test_abort_before_start():
    """If signal is already aborted before run(), should exit immediately."""
    abort = AbortSignal()
    abort.abort("pre-aborted")

    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("hi")],
        tools=[],
        provider=SlowProvider(),
        system_prompt="test",
        abort_signal=abort,
    ):
        events.append(event)

    result_event = next(
        (e for e in events if e.type == LoopEventType.result), None
    )
    assert result_event is not None
    assert "abort" in result_event.data.reason

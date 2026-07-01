"""Safety-net tests for AgentLoop: the loop detector stops runaway runs."""
import pytest

from chimera.core.agent_loop import AgentLoop
from chimera.detection.exact import ExactRepeatDetector
from chimera.providers.base import Response
from chimera.tools.think import ThinkTool
from chimera.types import Message, ToolCall


class _RepeatProvider:
    """Always returns the same tool call — an infinite loop without a bound."""

    model_name = "fake"
    context_window = 200_000

    def __init__(self):
        self.calls = 0

    async def async_complete(self, messages, tools=None):
        self.calls += 1
        return Response(
            content="x",
            tool_calls=[ToolCall(id=f"c{self.calls}", name="think",
                                 arguments={"thought": "same"})],
            usage={"input_tokens": 1, "output_tokens": 1},
        )


@pytest.mark.asyncio
async def test_loop_detector_stops_unlimited_run():
    provider = _RepeatProvider()
    last = None
    async for ev in AgentLoop().run(
        [Message.user("go")],
        tools=[ThinkTool()],
        provider=provider,
        system_prompt="s",
        max_turns=None,  # unlimited: only the detector can stop it
        loop_detector=ExactRepeatDetector(threshold=5),
        stream=False,
    ):
        last = ev
    assert last is not None
    assert last.data.reason == "loop_detected"
    assert provider.calls < 20  # stopped promptly, not infinite


@pytest.mark.asyncio
async def test_max_turns_still_bounds_without_detector():
    provider = _RepeatProvider()
    last = None
    async for ev in AgentLoop().run(
        [Message.user("go")],
        tools=[ThinkTool()],
        provider=provider,
        system_prompt="s",
        max_turns=3,
        stream=False,
    ):
        last = ev
    assert last.data.reason == "max_turns"

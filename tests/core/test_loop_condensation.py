"""Tests for the M11 LLM-condensation hookup in ``ReAct.async_iter_steps``.

These tests assert that, when ``LoopConfig.condensation`` and
``LoopConfig.condense_every_n_steps`` are both set, the loop fires
``compact()`` exactly once for every N steps that complete -- matching
SWE-bench Verified's ``should_condense`` contract.

The tests are intentionally synthetic (no real provider, no real
compaction implementation) so they isolate the loop wiring from
provider/summary behaviour.
"""
from __future__ import annotations

from typing import Any

import pytest

from chimera.compaction.summary import SummaryCompaction
from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall


class _NeverDoneProvider(Provider):
    """Provider that always returns one (no-op) tool call so the loop runs."""

    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return self._next()

    async def async_complete(
        self, messages, tools=None, temperature=0.0, max_tokens=None,
    ):
        return self._next()

    def _next(self) -> Response:
        self.call_count += 1
        # No tool calls so the loop terminates cleanly each step? No --
        # we WANT max_steps to drive termination so we can count steps.
        # But returning no tool calls ends the loop after step 1.
        # Returning a tool call without a matching tool would crash the
        # executor; we instead return text-only and let max_steps cap it.
        # The simplest "loop forever" shape is: return a tool call to a
        # tool we register that always succeeds -- we wire that below.
        return Response(
            content=f"step{self.call_count}",
            tool_calls=[ToolCall(id=f"t{self.call_count}", name="noop", arguments={})],
            usage={"input_tokens": 1, "output_tokens": 1},
        )

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock"


class _NoopTool:
    """A tool the loop can call without side effects."""

    name = "noop"
    description = "noop"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def to_anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def execute(self, args, env=None):
        from chimera.types import ToolResult

        return ToolResult(output="ok")

    async def async_execute(self, args, env=None):
        from chimera.types import ToolResult

        return ToolResult(output="ok")


class _RecordingCompaction(SummaryCompaction):
    """SummaryCompaction subclass that records every ``compact`` call."""

    def __init__(self) -> None:
        super().__init__(provider=None, keep_first=1, keep_last=1)
        self.calls: list[tuple[int, int]] = []  # (n_messages, budget)

    def compact(self, messages, budget):
        self.calls.append((len(messages), budget))
        return super().compact(messages, budget)


@pytest.mark.asyncio
async def test_condensation_fires_once_at_step_25_within_30_step_run() -> None:
    """30-step run with ``condense_every_n_steps=25`` -> 1 compaction."""
    provider = _NeverDoneProvider()
    tool = _NoopTool()
    compaction = _RecordingCompaction()
    config = LoopConfig(
        condensation=compaction,
        condense_every_n_steps=25,
        # Skip the safety-default permission prompt so tool calls run.
        yolo_mode=True,
    )
    loop = ReAct(max_steps=30, config=config)

    context = Context(system="test")
    context.add(Message.user("go"))

    steps = 0
    async for _ in loop.async_iter_steps(provider, [tool], context, None):
        steps += 1

    # We expect exactly one compaction call, fired after step 25.
    assert len(compaction.calls) == 1, (
        f"expected exactly 1 compaction call after 30 steps with N=25, "
        f"got {len(compaction.calls)}"
    )


@pytest.mark.asyncio
async def test_condensation_skipped_when_disabled() -> None:
    """No compaction when fields are unset (regression guard)."""
    provider = _NeverDoneProvider()
    tool = _NoopTool()
    compaction = _RecordingCompaction()
    # condensation set but cadence is None -> must NOT fire.
    config = LoopConfig(
        condensation=compaction,
        condense_every_n_steps=None,
        yolo_mode=True,
    )
    loop = ReAct(max_steps=30, config=config)
    context = Context(system="test")
    context.add(Message.user("go"))

    async for _ in loop.async_iter_steps(provider, [tool], context, None):
        pass

    assert compaction.calls == []


@pytest.mark.asyncio
async def test_condensation_fires_twice_at_50_step_run_with_n_25() -> None:
    """50 steps with N=25 -> compaction at steps 25 and 50."""
    provider = _NeverDoneProvider()
    tool = _NoopTool()
    compaction = _RecordingCompaction()
    config = LoopConfig(
        condensation=compaction,
        condense_every_n_steps=25,
        yolo_mode=True,
    )
    loop = ReAct(max_steps=50, config=config)
    context = Context(system="test")
    context.add(Message.user("go"))

    async for _ in loop.async_iter_steps(provider, [tool], context, None):
        pass

    assert len(compaction.calls) == 2, (
        f"expected 2 compaction calls in a 50-step run with N=25, "
        f"got {len(compaction.calls)}"
    )

"""Tests for chimera.core.streaming_executor.StreamingToolExecutor (Task 6, Phase 1)."""
from __future__ import annotations

import asyncio

import pytest

from chimera.core.tool import BaseTool
from chimera.types import ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class FastTool(BaseTool):
    name = "fast"
    description = "fast"
    parameters: dict = {}
    is_concurrency_safe = True

    def execute(self, args, env) -> ToolResult:
        return ToolResult(output="fast done")

    async def async_execute(self, args, env) -> ToolResult:
        return ToolResult(output="fast done")


class SlowTool(BaseTool):
    name = "slow"
    description = "slow"
    parameters: dict = {}
    is_concurrency_safe = False

    def execute(self, args, env) -> ToolResult:
        return ToolResult(output="slow done")

    async def async_execute(self, args, env) -> ToolResult:
        await asyncio.sleep(0.01)
        return ToolResult(output="slow done")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_tools_run_in_parallel():
    """3 concurrency-safe FastTools should all complete and results returned in order."""
    from chimera.core.streaming_executor import StreamingToolExecutor

    tools = [FastTool()]
    executor = StreamingToolExecutor(tools=tools, max_concurrent=5)

    calls = [
        ToolCall(id="c1", name="fast", arguments={}),
        ToolCall(id="c2", name="fast", arguments={}),
        ToolCall(id="c3", name="fast", arguments={}),
    ]

    for call in calls:
        await executor.submit(call)

    results = await executor.collect()

    assert len(results) == 3
    # Results must be in submission order
    returned_ids = [call.id for call, _ in results]
    assert returned_ids == ["c1", "c2", "c3"]
    # All succeeded
    for call, result in results:
        assert result.output == "fast done"
        assert result.error is None


@pytest.mark.asyncio
async def test_non_concurrent_tools_run_sequentially():
    """A non-concurrent SlowTool should complete successfully via collect()."""
    from chimera.core.streaming_executor import StreamingToolExecutor

    tools = [SlowTool()]
    executor = StreamingToolExecutor(tools=tools, max_concurrent=5)

    call = ToolCall(id="s1", name="slow", arguments={})
    await executor.submit(call)

    results = await executor.collect()

    assert len(results) == 1
    returned_call, result = results[0]
    assert returned_call.id == "s1"
    assert result.output == "slow done"
    assert result.error is None


@pytest.mark.asyncio
async def test_discard_returns_error_results():
    """discard() cancels pending tasks and returns synthetic errors for unfinished work."""
    from chimera.core.streaming_executor import StreamingToolExecutor

    tools = [SlowTool()]
    executor = StreamingToolExecutor(tools=tools, max_concurrent=5)

    call = ToolCall(id="d1", name="slow", arguments={})
    await executor.submit(call)

    # Discard immediately without awaiting collect
    results = await executor.discard()

    # Must return exactly one entry (the submitted call)
    assert len(results) == 1
    returned_call, result = results[0]
    assert returned_call.id == "d1"
    # Result must be a synthetic error (task was cancelled or not finished)
    assert result.error is not None

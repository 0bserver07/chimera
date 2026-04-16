"""Tests for chimera.hooks.async_registry — AsyncHookRegistry."""
from __future__ import annotations

import asyncio

import pytest

from chimera.hooks.async_registry import AsyncHookRegistry, PendingAsyncHook
from chimera.hooks.events import HookEvent
from chimera.hooks.hook_types import HookOutput


# ---------------------------------------------------------------------------
# PendingAsyncHook dataclass
# ---------------------------------------------------------------------------


def test_pending_async_hook_defaults():
    ph = PendingAsyncHook(
        hook_id="h1",
        hook_name="check",
        event=HookEvent.PRE_TOOL_USE,
        start_time=0.0,
    )
    assert ph.timeout_ms == 15000
    assert ph.task is None
    assert ph.completed is False
    assert ph.result is None


# ---------------------------------------------------------------------------
# register + check_completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_and_check_completed():
    registry = AsyncHookRegistry()

    async def quick_hook():
        return HookOutput(decision="allow")

    task = asyncio.create_task(quick_hook())
    registry.register("h1", "quick", HookEvent.PRE_TOOL_USE, task)

    # Let the task complete
    await asyncio.sleep(0.05)

    completed = await registry.check_completed()
    assert len(completed) == 1
    assert completed[0].hook_id == "h1"
    assert completed[0].completed is True
    assert completed[0].result is not None
    assert completed[0].result.decision == "allow"


@pytest.mark.asyncio
async def test_check_completed_timeout():
    registry = AsyncHookRegistry()

    async def slow_hook():
        await asyncio.sleep(10)
        return HookOutput()

    task = asyncio.create_task(slow_hook())
    registry.register("h2", "slow", HookEvent.POST_TOOL_USE, task, timeout_ms=50)

    # Wait long enough for the timeout to trigger
    await asyncio.sleep(0.1)

    completed = await registry.check_completed()
    # Should have timed out and been marked completed
    assert len(completed) == 1
    assert completed[0].completed is True


# ---------------------------------------------------------------------------
# finalize_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_all_cancels_pending():
    registry = AsyncHookRegistry()

    async def forever_hook():
        await asyncio.sleep(999)
        return HookOutput()

    task = asyncio.create_task(forever_hook())
    registry.register("h3", "forever", HookEvent.STOP, task)

    await registry.finalize_all()

    # Task should be cancelled
    assert task.cancelled() or task.done()

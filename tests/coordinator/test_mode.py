"""Tests for chimera.coordinator.mode — CoordinatorMode dispatch + IG-12."""
from __future__ import annotations

import asyncio

import pytest

from chimera.core.abort import AbortSignal
from chimera.core.agent_context import AgentContext
from chimera.core.agent_definition import AgentDefinition
from chimera.core.feature_flags import FeatureFlags
from chimera.core.loop_events import LoopEvent, LoopEventType
from chimera.core.loop_state import QuerySource
from chimera.coordinator.mode import CoordinatorMode


def _make_parent_context() -> AgentContext:
    return AgentContext(
        messages=[],
        file_state_cache={},
        abort_signal=AbortSignal(),
        denial_tracking={},
        agent_id="parent",
        parent_agent_id=None,
        query_source=QuerySource.FOREGROUND,
        depth=0,
        get_app_state=lambda: {},
        set_app_state=lambda u: None,
        set_app_state_for_tasks=lambda u: None,
    )


class _FakeSpawner:
    """Minimal spawner that yields a system event then completes."""

    def __init__(self, delay: float = 0.0) -> None:
        self._delay = delay

    async def spawn(self, definition, prompt, parent_context, **kwargs):
        if self._delay:
            await asyncio.sleep(self._delay)
        yield LoopEvent(
            type=LoopEventType.system,
            data={"event": "done"},
            turn=0,
        )


class TestCoordinatorMode:

    def setup_method(self) -> None:
        FeatureFlags.reset()

    def teardown_method(self) -> None:
        FeatureFlags.reset()

    def test_disabled_by_default(self) -> None:
        """CoordinatorMode.is_enabled is False when the flag is not set."""
        coord = CoordinatorMode(spawner=None, agent_definitions={})
        assert not coord.is_enabled

    def test_enabled_with_flag(self) -> None:
        """CoordinatorMode.is_enabled is True when the flag is set."""
        FeatureFlags.set("COORDINATOR_MODE", True)
        coord = CoordinatorMode(spawner=None, agent_definitions={})
        assert coord.is_enabled

    @pytest.mark.asyncio
    async def test_dispatch_raises_when_disabled(self) -> None:
        coord = CoordinatorMode(spawner=None, agent_definitions={})
        with pytest.raises(RuntimeError, match="not enabled"):
            await coord.dispatch("task", "worker", _make_parent_context())

    @pytest.mark.asyncio
    async def test_dispatch_raises_when_no_spawner(self) -> None:
        FeatureFlags.set("COORDINATOR_MODE", True)
        coord = CoordinatorMode(spawner=None, agent_definitions={})
        with pytest.raises(RuntimeError, match="No spawner"):
            await coord.dispatch("task", "worker", _make_parent_context())

    @pytest.mark.asyncio
    async def test_get_status_unknown_agent(self) -> None:
        """get_status returns 'unknown' for untracked agent IDs."""
        coord = CoordinatorMode(spawner=None, agent_definitions={})
        status = await coord.get_status("nonexistent-id")
        assert status == "unknown"

    @pytest.mark.asyncio
    async def test_get_status_done_after_dispatch(self) -> None:
        """After a fast background dispatch completes, status is 'done'."""
        FeatureFlags.set("COORDINATOR_MODE", True)
        defn = AgentDefinition(name="worker", description="A worker agent")
        spawner = _FakeSpawner(delay=0.0)
        coord = CoordinatorMode(spawner=spawner, agent_definitions={"worker": defn})
        agent_id = await coord.dispatch(
            "do stuff", "worker", _make_parent_context(), run_in_background=True,
        )
        # Give the task a moment to complete
        await asyncio.sleep(0.05)
        status = await coord.get_status(agent_id)
        assert status == "done"

    @pytest.mark.asyncio
    async def test_cancel_running_agent(self) -> None:
        """cancel() stops a long-running background agent."""
        FeatureFlags.set("COORDINATOR_MODE", True)
        defn = AgentDefinition(name="worker", description="A worker agent")
        spawner = _FakeSpawner(delay=10.0)  # long delay
        coord = CoordinatorMode(spawner=spawner, agent_definitions={"worker": defn})
        agent_id = await coord.dispatch(
            "long task", "worker", _make_parent_context(), run_in_background=True,
        )
        # Should be running
        status = await coord.get_status(agent_id)
        assert status == "running"

        # Cancel it
        await coord.cancel(agent_id)
        status = await coord.get_status(agent_id)
        assert status == "done"

    @pytest.mark.asyncio
    async def test_cancel_unknown_agent_is_noop(self) -> None:
        """cancel() on an unknown agent_id does nothing."""
        coord = CoordinatorMode(spawner=None, agent_definitions={})
        # Should not raise
        await coord.cancel("nonexistent-id")

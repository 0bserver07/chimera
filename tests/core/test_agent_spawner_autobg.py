"""Tests for AutoBackgroundMonitor integration in AgentSpawner."""
from __future__ import annotations

import asyncio

import pytest

from chimera.core.abort import AbortSignal
from chimera.core.agent_context import AgentContext
from chimera.core.agent_definition import AgentDefinition
from chimera.core.agent_spawner import AgentSpawner
from chimera.core.auto_background import AutoBackgroundConfig
from chimera.core.loop_events import LoopEventType
from chimera.core.loop_state import QuerySource
from chimera.core.task_manager import TaskManager
from chimera.providers.base import Response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class SlowProvider:
    """Provider that delays responses to simulate long-running agents."""

    def __init__(self, delay_seconds: float, responses: list[Response]) -> None:
        self._delay = delay_seconds
        self._responses = iter(responses)
        self.model_name = "slow"

    async def async_complete(self, messages, tools=None, **kwargs):
        await asyncio.sleep(self._delay)
        return next(self._responses)


class FastProvider:
    """Provider that returns immediately."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = iter(responses)
        self.model_name = "fast"

    async def async_complete(self, messages, tools=None, **kwargs):
        return next(self._responses)


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
# Tests
# ---------------------------------------------------------------------------


class TestAutoBackgroundInSpawner:
    @pytest.mark.asyncio
    async def test_auto_background_triggers_after_threshold(self):
        """When agent runs longer than threshold, it should be auto-backgrounded."""
        definition = AgentDefinition(
            name="slow-agent",
            description="Takes a while",
            system_prompt="You are slow.",
        )

        # Provider that takes 0.15s — threshold is 50ms
        provider = SlowProvider(
            delay_seconds=0.15,
            responses=[
                Response(content="Done slowly", tool_calls=[], usage={}),
            ],
        )

        auto_bg_config = AutoBackgroundConfig(threshold_ms=50, enabled=True)
        spawner = AgentSpawner(
            provider=provider,
            available_tools=[],
            task_manager=TaskManager(),
            auto_bg_config=auto_bg_config,
        )

        parent_ctx = _make_parent_context()
        events = []
        async for event in spawner.spawn(
            definition=definition,
            prompt="Do something slowly",
            parent_context=parent_ctx,
        ):
            events.append(event)

        # Should see an auto-backgrounded system message
        system_events = [
            e for e in events
            if e.type == LoopEventType.system
            and "auto-background" in str(e.data).lower()
        ]
        assert len(system_events) >= 1

    @pytest.mark.asyncio
    async def test_no_auto_background_when_fast(self):
        """When agent finishes quickly, no auto-backgrounding occurs."""
        definition = AgentDefinition(
            name="fast-agent",
            description="Quick",
            system_prompt="You are fast.",
        )

        provider = FastProvider(
            responses=[
                Response(content="Done fast", tool_calls=[], usage={}),
            ],
        )

        auto_bg_config = AutoBackgroundConfig(threshold_ms=10_000, enabled=True)
        spawner = AgentSpawner(
            provider=provider,
            available_tools=[],
            task_manager=TaskManager(),
            auto_bg_config=auto_bg_config,
        )

        parent_ctx = _make_parent_context()
        events = []
        async for event in spawner.spawn(
            definition=definition,
            prompt="Do something fast",
            parent_context=parent_ctx,
        ):
            events.append(event)

        # Should have normal completion, no auto-background event
        system_events = [
            e for e in events
            if e.type == LoopEventType.system
            and "auto-background" in str(e.data).lower()
        ]
        assert len(system_events) == 0

        result_events = [e for e in events if e.type == LoopEventType.result]
        assert len(result_events) == 1
        assert result_events[0].data.reason == "completed"

    @pytest.mark.asyncio
    async def test_no_auto_background_when_disabled(self):
        """When auto-background is disabled, it should not trigger."""
        definition = AgentDefinition(
            name="slow-agent",
            description="Takes a while",
            system_prompt="test",
        )

        provider = SlowProvider(
            delay_seconds=0.15,
            responses=[
                Response(content="Done", tool_calls=[], usage={}),
            ],
        )

        auto_bg_config = AutoBackgroundConfig(threshold_ms=50, enabled=False)
        spawner = AgentSpawner(
            provider=provider,
            available_tools=[],
            task_manager=TaskManager(),
            auto_bg_config=auto_bg_config,
        )

        parent_ctx = _make_parent_context()
        events = []
        async for event in spawner.spawn(
            definition=definition,
            prompt="Do something slowly",
            parent_context=parent_ctx,
        ):
            events.append(event)

        system_events = [
            e for e in events
            if e.type == LoopEventType.system
            and "auto-background" in str(e.data).lower()
        ]
        assert len(system_events) == 0

    @pytest.mark.asyncio
    async def test_no_auto_background_without_config(self):
        """Without auto_bg_config, spawner should work normally."""
        definition = AgentDefinition(
            name="normal-agent",
            description="Normal",
            system_prompt="test",
        )

        provider = FastProvider(
            responses=[
                Response(content="ok", tool_calls=[], usage={}),
            ],
        )

        spawner = AgentSpawner(
            provider=provider,
            available_tools=[],
            task_manager=TaskManager(),
            # No auto_bg_config
        )

        parent_ctx = _make_parent_context()
        events = []
        async for event in spawner.spawn(
            definition=definition,
            prompt="test",
            parent_context=parent_ctx,
        ):
            events.append(event)

        result_events = [e for e in events if e.type == LoopEventType.result]
        assert len(result_events) == 1

    @pytest.mark.asyncio
    async def test_auto_background_not_triggered_in_background_mode(self):
        """Auto-background should only apply to foreground spawns."""
        definition = AgentDefinition(
            name="bg-agent",
            description="Background agent",
            system_prompt="test",
        )

        provider = SlowProvider(
            delay_seconds=0.15,
            responses=[
                Response(content="Done", tool_calls=[], usage={}),
            ],
        )

        auto_bg_config = AutoBackgroundConfig(threshold_ms=50, enabled=True)
        task_manager = TaskManager()
        spawner = AgentSpawner(
            provider=provider,
            available_tools=[],
            task_manager=task_manager,
            auto_bg_config=auto_bg_config,
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

        # Background spawn yields single system event, not auto-background
        assert len(events) == 1
        assert events[0].type == LoopEventType.system
        assert "async_launched" in str(events[0].data)

        # Wait for background task to finish
        await asyncio.sleep(0.3)

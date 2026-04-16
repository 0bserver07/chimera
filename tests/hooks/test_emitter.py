"""Tests for chimera.hooks.emitter — centralized hook emission."""
from __future__ import annotations

import pytest

from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import (
    FunctionHook,
    HookInput,
    HookMatcher,
    HookOutput,
)


# ---------------------------------------------------------------------------
# Tests: no executor configured
# ---------------------------------------------------------------------------


class TestNoExecutor:
    @pytest.mark.asyncio
    async def test_emit_returns_default_output_without_executor(self):
        """When no executor is set, emit() should return a default HookOutput."""
        emitter = HookEmitter()
        result = await emitter.emit(HookEvent.SESSION_START, session_id="s1")
        assert isinstance(result, HookOutput)
        assert result.continue_execution is True

    def test_active_property_false_without_executor(self):
        emitter = HookEmitter()
        assert emitter.active is False


# ---------------------------------------------------------------------------
# Tests: with executor
# ---------------------------------------------------------------------------


class TestWithExecutor:
    def test_active_property_true_with_executor(self):
        executor = HookExecutor()
        emitter = HookEmitter(executor=executor)
        assert emitter.active is True

    @pytest.mark.asyncio
    async def test_emit_fires_matching_hooks(self):
        """emit() should call matching hooks through the executor."""
        results = []

        def capture(messages, abort):
            results.append("fired")
            return HookOutput()

        matcher = HookMatcher(
            hooks=[FunctionHook(callback=capture, timeout=5)],
            matcher=None,  # matches everything
        )
        executor = HookExecutor()
        emitter = HookEmitter(executor=executor, matchers=[matcher])

        output = await emitter.emit(HookEvent.SUBAGENT_START, session_id="s1", tool_name="test")
        assert isinstance(output, HookOutput)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_emit_passes_session_id(self):
        """emit() should pass session_id to the HookInput."""
        captured_inputs = []

        original_execute = HookExecutor.execute

        async def spy_execute(self, event, input_data, matchers, abort_signal=None):
            captured_inputs.append(input_data)
            return HookOutput()

        executor = HookExecutor()
        emitter = HookEmitter(executor=executor)

        # Monkey-patch for inspection
        executor.execute = spy_execute.__get__(executor, HookExecutor)
        await emitter.emit(HookEvent.SESSION_START, session_id="test-session-42")

        assert len(captured_inputs) == 1
        assert captured_inputs[0].session_id == "test-session-42"

    @pytest.mark.asyncio
    async def test_emit_passes_extra_kwargs(self):
        """Extra kwargs should be passed through to HookInput."""
        captured_inputs = []

        async def spy_execute(self, event, input_data, matchers, abort_signal=None):
            captured_inputs.append(input_data)
            return HookOutput()

        executor = HookExecutor()
        emitter = HookEmitter(executor=executor)

        executor.execute = spy_execute.__get__(executor, HookExecutor)
        await emitter.emit(
            HookEvent.SUBAGENT_START,
            session_id="s1",
            tool_name="sub-agent-x",
        )

        assert captured_inputs[0].tool_name == "sub-agent-x"

    @pytest.mark.asyncio
    async def test_emit_with_no_matchers(self):
        """emit() with an executor but no matchers should return default HookOutput."""
        executor = HookExecutor()
        emitter = HookEmitter(executor=executor, matchers=[])

        result = await emitter.emit(HookEvent.SESSION_START, session_id="s1")
        assert result.continue_execution is True

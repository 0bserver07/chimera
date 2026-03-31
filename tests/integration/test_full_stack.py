"""Full-stack integration test — verifies all Phase 1-8 modules wire together."""
from __future__ import annotations

import pytest
import asyncio

from chimera.core.agent_loop import AgentLoop
from chimera.core.loop_events import LoopEventType
from chimera.core.abort import AbortSignal
from chimera.core.feature_flags import FeatureFlags
from chimera.core.system_prompt import SystemPromptBuilder
from chimera.core.memory import PersistentMemory
from chimera.hooks.executor import HookExecutor
from chimera.hooks.types import HookMatcher, FunctionHook, HookInput, HookOutput
from chimera.hooks.events import HookEvent
from chimera.permissions.checker import PermissionChecker
from chimera.commands.registry import CommandRegistry
from chimera.core.agent_context import AgentContext, IsolationLevel
from chimera.core.content_replacement import ContentReplacementState
from chimera.types import Message, ToolCall, ToolResult
from chimera.providers.base import Response
from chimera.core.tool import BaseTool


class MockProvider:
    model_name = "mock"

    async def async_complete(self, messages, tools=None, **kw):
        return Response(content="Done!", tool_calls=[], usage={})


@pytest.mark.asyncio
async def test_full_stack_imports():
    """Verify all Phase 1-8 modules can be imported together."""
    # If this doesn't raise ImportError, all modules are wired correctly
    assert AgentLoop is not None
    assert AbortSignal is not None
    assert FeatureFlags is not None
    assert SystemPromptBuilder is not None
    assert HookExecutor is not None
    assert PermissionChecker is not None
    assert CommandRegistry is not None
    assert AgentContext is not None
    assert ContentReplacementState is not None
    assert PersistentMemory is not None


@pytest.mark.asyncio
async def test_agent_loop_with_hooks_and_prompt():
    """End-to-end: AgentLoop with system prompt builder and hooks."""
    # Build system prompt
    prompt = SystemPromptBuilder().add_layer("default", "You are helpful.").build()

    # Set up a function hook that tracks calls
    hook_calls = []

    def track_hook(messages, signal=None):
        hook_calls.append("stop_fired")
        return True  # Allow completion

    executor = HookExecutor()
    matchers = [
        HookMatcher(
            matcher=None,
            hooks=[FunctionHook(callback=track_hook, timeout=5)],
        )
    ]

    provider = MockProvider()
    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("Hi")],
        tools=[],
        provider=provider,
        system_prompt=prompt.to_string(),
        hook_executor=executor,
        hook_matchers=matchers,
    ):
        events.append(event)

    result = next(e for e in events if e.type == LoopEventType.result)
    assert result.data.reason == "completed"


@pytest.mark.asyncio
async def test_feature_flags_gate_behavior():
    """Feature flags control feature availability."""
    FeatureFlags.reset()
    assert not FeatureFlags.enabled("COORDINATOR_MODE")
    FeatureFlags.override("COORDINATOR_MODE", True)
    assert FeatureFlags.enabled("COORDINATOR_MODE")
    FeatureFlags.reset()

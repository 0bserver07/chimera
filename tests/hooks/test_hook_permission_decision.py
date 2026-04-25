"""Tests for PreToolUse hook permission decision overrides.

Covers:
    - permissionDecision="deny" raises PermissionDenied at dispatch.
    - permissionDecision="allow" overrides a default-deny policy.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chimera.core.context import Context
from chimera.core.loop_config import LoopConfig
from chimera.core.tool_executor import (
    PermissionDenied,
    execute_tool_calls,
)
from chimera.hooks.emitter import HookEmitter
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import (
    FunctionHook,
    HookMatcher,
    HookOutput,
)
from chimera.permissions.presets import AlwaysDeny, AutoApprove
from chimera.types import ToolCall, ToolResult


def _make_recording_tool(name: str = "bash"):
    tool = MagicMock()
    tool.name = name
    tool.execute.return_value = ToolResult(output="ok")
    return tool


def _emitter(callback) -> HookEmitter:
    matcher = HookMatcher(hooks=[FunctionHook(callback=callback)])
    return HookEmitter(executor=HookExecutor(), matchers=[matcher])


# ---------------------------------------------------------------------------
# Test 1: permissionDecision = "deny" blocks tool dispatch
# ---------------------------------------------------------------------------


def test_decision_deny_blocks_tool() -> None:
    """A hook returning permission_decision='deny' raises PermissionDenied."""

    def deny_hook(messages, abort):
        return HookOutput(
            continue_execution=False,
            permission_decision="deny",
            permission_decision_reason="policy says no",
        )

    config = LoopConfig(
        permissions=AutoApprove(),  # would otherwise allow
        hook_emitter=_emitter(deny_hook),
    )

    tool = _make_recording_tool("bash")
    tc = ToolCall(id="c1", name="bash", arguments={"command": "ls"})

    with pytest.raises(PermissionDenied) as excinfo:
        execute_tool_calls([tc], {"bash": tool}, Context(), None, config)

    assert excinfo.value.tool_name == "bash"
    tool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: permissionDecision = "allow" overrides AlwaysDeny default
# ---------------------------------------------------------------------------


def test_decision_allow_overrides_default_deny() -> None:
    """A hook returning permission_decision='allow' bypasses AlwaysDeny."""

    def allow_hook(messages, abort):
        return HookOutput(
            continue_execution=True,
            permission_decision="allow",
            permission_decision_reason="trusted source",
        )

    config = LoopConfig(
        permissions=AlwaysDeny(),  # would block everything
        hook_emitter=_emitter(allow_hook),
    )

    tool = _make_recording_tool("bash")
    tc = ToolCall(id="c2", name="bash", arguments={"command": "ls"})

    execute_tool_calls([tc], {"bash": tool}, Context(), None, config)

    # Tool ran in spite of AlwaysDeny because the hook said allow.
    tool.execute.assert_called_once()

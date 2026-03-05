"""Tests for LoopConfig new fields and tool_executor audit/checkpoint hooks."""
from __future__ import annotations

from unittest.mock import MagicMock

from chimera.core.context import Context
from chimera.core.loop_config import LoopConfig
from chimera.core.tool_executor import execute_tool_calls
from chimera.types import ToolCall, ToolResult


# --- LoopConfig field tests ---


def test_loop_config_audit_log_field():
    config = LoopConfig(audit_log=MagicMock())
    assert config.audit_log is not None


def test_loop_config_checkpoint_manager_field():
    config = LoopConfig(checkpoint_manager=MagicMock())
    assert config.checkpoint_manager is not None


def test_loop_config_git_workflow_field():
    config = LoopConfig(git_workflow=MagicMock())
    assert config.git_workflow is not None


# --- Hook integration tests ---


def _make_tool(name: str = "test_tool", success: bool = True):
    tool = MagicMock()
    tool.name = name
    tool.execute.return_value = ToolResult(output="ok", error=None if success else "fail")
    return tool


def test_audit_hook_records():
    audit = MagicMock()
    config = LoopConfig(audit_log=audit)
    tool = _make_tool("read")
    tc = ToolCall(id="c1", name="read", arguments={"path": "/tmp"})
    ctx = Context()

    execute_tool_calls([tc], {"read": tool}, ctx, None, config)

    audit.record.assert_called_once_with(
        tool_name="read", arguments={"path": "/tmp"}, decision="allowed"
    )


def test_checkpoint_hook_creates():
    cp = MagicMock()
    config = LoopConfig(checkpoint_manager=cp)
    tool = _make_tool("write")
    tc = ToolCall(id="c2", name="write", arguments={"content": "hi"})
    ctx = Context()

    execute_tool_calls([tc], {"write": tool}, ctx, None, config)

    cp.create.assert_called_once_with(description="After write")


def test_checkpoint_hook_skipped_on_failure():
    cp = MagicMock()
    config = LoopConfig(checkpoint_manager=cp)
    tool = _make_tool("bash", success=False)
    tc = ToolCall(id="c3", name="bash", arguments={"cmd": "false"})
    ctx = Context()

    execute_tool_calls([tc], {"bash": tool}, ctx, None, config)

    cp.create.assert_not_called()

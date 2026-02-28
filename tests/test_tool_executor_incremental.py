"""Tests for execute_tool_calls_incremental()."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.core.context import Context
from chimera.core.tool import BaseTool
from chimera.core.tool_executor import (
    LoopBreak,
    ToolExecutionResult,
    execute_tool_calls_incremental,
)
from chimera.env.base import Environment
from chimera.types import ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class EchoTool(BaseTool):
    name = "echo"
    description = "Echo args"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return ToolResult(output=f"echo: {args}")


class FailTool(BaseTool):
    name = "fail"
    description = "Always fails"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return ToolResult(output="", error="boom")


def make_tc(name: str = "echo", call_id: str = "c1", **kwargs: Any) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBasicExecution:
    def test_single_tool(self) -> None:
        ctx = Context()
        result = execute_tool_calls_incremental(
            [make_tc()], {"echo": EchoTool()}, ctx, None, None,
        )
        assert result.executed == 1
        assert len(result.results) == 1
        assert result.results[0].success
        assert result.pending is None
        assert result.remaining == []

    def test_multiple_tools(self) -> None:
        ctx = Context()
        tcs = [make_tc(call_id="c1"), make_tc(call_id="c2")]
        result = execute_tool_calls_incremental(
            tcs, {"echo": EchoTool()}, ctx, None, None,
        )
        assert result.executed == 2
        assert len(result.results) == 2

    def test_unknown_tool(self) -> None:
        ctx = Context()
        result = execute_tool_calls_incremental(
            [make_tc(name="unknown")], {"echo": EchoTool()}, ctx, None, None,
        )
        assert result.executed == 1
        assert result.results[0].error is not None

    def test_error_tool(self) -> None:
        ctx = Context()
        result = execute_tool_calls_incremental(
            [make_tc(name="fail")], {"fail": FailTool()}, ctx, None, None,
        )
        assert result.executed == 1
        assert not result.results[0].success


class TestPermissionDeny:
    def test_deny_skips_no_exception(self) -> None:
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.base import PermissionAction

        policy = MagicMock()
        policy.evaluate.return_value = PermissionAction.DENY
        config = LoopConfig(permissions=policy)

        ctx = Context()
        result = execute_tool_calls_incremental(
            [make_tc()], {"echo": EchoTool()}, ctx, None, config,
        )
        assert result.executed == 0
        assert result.pending is None
        assert len(result.results) == 1  # denial result added


class TestPermissionAsk:
    def test_ask_returns_pending(self) -> None:
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.base import PermissionAction

        policy = MagicMock()
        policy.evaluate.return_value = PermissionAction.ASK
        config = LoopConfig(permissions=policy)

        ctx = Context()
        tcs = [make_tc(call_id="c1"), make_tc(call_id="c2")]
        result = execute_tool_calls_incremental(
            tcs, {"echo": EchoTool()}, ctx, None, config,
        )
        assert result.pending is not None
        assert result.pending.tool_name == "echo"
        assert len(result.remaining) == 1  # second call is remaining

    def test_ask_middle_tool_call(self) -> None:
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.base import PermissionAction

        # First call: ALLOW, second: ASK, third: never reached
        policy = MagicMock()
        policy.evaluate.side_effect = [
            PermissionAction.ALLOW, PermissionAction.ASK, PermissionAction.ALLOW,
        ]
        config = LoopConfig(permissions=policy)

        ctx = Context()
        tcs = [make_tc(call_id="c1"), make_tc(call_id="c2"), make_tc(call_id="c3")]
        result = execute_tool_calls_incremental(
            tcs, {"echo": EchoTool()}, ctx, None, config,
        )
        assert result.executed == 1  # Only first executed
        assert result.pending is not None
        assert result.pending.tool_call.id == "c2"
        assert len(result.remaining) == 1  # c3 remaining


class TestPermissionAllow:
    def test_allow_executes_normally(self) -> None:
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.base import PermissionAction

        policy = MagicMock()
        policy.evaluate.return_value = PermissionAction.ALLOW
        config = LoopConfig(permissions=policy)

        ctx = Context()
        result = execute_tool_calls_incremental(
            [make_tc()], {"echo": EchoTool()}, ctx, None, config,
        )
        assert result.executed == 1
        assert result.pending is None


class TestLoopBreak:
    def test_loop_break_still_raises(self) -> None:
        from chimera.core.loop_config import LoopConfig
        from chimera.detection.actions import OnDetect

        detector = MagicMock()
        det_result = MagicMock()
        det_result.pattern = "repeated bash"
        detector.record_and_check.return_value = det_result
        detector.on_detect = OnDetect.BREAK
        config = LoopConfig(detector=detector)

        ctx = Context()
        with pytest.raises(LoopBreak):
            execute_tool_calls_incremental(
                [make_tc()], {"echo": EchoTool()}, ctx, None, config,
            )


class TestToolExecutionResultDefaults:
    def test_defaults(self) -> None:
        r = ToolExecutionResult()
        assert r.executed == 0
        assert r.results == []
        assert r.pending is None
        assert r.remaining == []

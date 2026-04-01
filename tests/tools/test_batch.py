"""Tests for chimera.tools.batch — Phase 9."""
from __future__ import annotations

from typing import Any

import pytest

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.tools.batch import BatchTool
from chimera.types import ToolResult


class EchoTool(BaseTool):
    """Trivial tool for testing."""
    name = "echo"
    description = "Echo input"
    parameters: dict[str, Any] = {"type": "object", "properties": {"msg": {"type": "string"}}}

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return ToolResult(output=args.get("msg", ""))


class TestBatchTool:
    """BatchTool runs multiple tool calls."""

    def test_batch_runs_multiple_tools(self):
        echo = EchoTool()
        batch = BatchTool(tool_map={"echo": echo})
        result = batch.execute(
            {"calls": [
                {"tool": "echo", "arguments": {"msg": "hello"}},
                {"tool": "echo", "arguments": {"msg": "world"}},
            ]},
            env=None,
        )
        assert result.error is None
        assert "[echo] hello" in result.output
        assert "[echo] world" in result.output

    def test_batch_handles_unknown_tool(self):
        batch = BatchTool(tool_map={})
        result = batch.execute(
            {"calls": [{"tool": "nonexistent", "arguments": {}}]},
            env=None,
        )
        assert "Unknown tool" in result.output

    @pytest.mark.asyncio
    async def test_batch_async_parallel(self):
        echo = EchoTool()
        batch = BatchTool(tool_map={"echo": echo})
        result = await batch.async_execute(
            {"calls": [
                {"tool": "echo", "arguments": {"msg": "alpha"}},
                {"tool": "echo", "arguments": {"msg": "beta"}},
            ]},
            env=None,
        )
        assert "[echo] alpha" in result.output
        assert "[echo] beta" in result.output

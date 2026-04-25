"""Tests for the per-tool-call timeout wired by audit H-4.

Covers:
* A long-running stub tool times out and returns a synthetic error result
  when ``LoopConfig.tool_timeout_s`` is set, instead of crashing the loop.
* Without ``tool_timeout_s`` the same stub runs to completion.
* ``chimera mink --tool-timeout`` is accepted by the CLI parser.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Any

import pytest

from chimera.core.context import Context
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.core.tool_executor import async_execute_tool_calls_incremental
from chimera.permissions.presets import AutoApprove
from chimera.types import ToolCall, ToolResult

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _SleepTool(BaseTool):
    """Stub tool that sleeps for a fixed duration before returning."""

    name = "sleeper"
    description = "Sleep for ``duration`` seconds (test stub)."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"duration": {"type": "number"}},
    }

    def execute(self, args: dict[str, Any], env: Any) -> ToolResult:
        # Sync path is unused in this test; required only for ABC contract.
        import time

        time.sleep(float(args.get("duration", 0.0)))
        return ToolResult(output="slept (sync)")

    async def async_execute(self, args: dict[str, Any], env: Any) -> ToolResult:
        await asyncio.sleep(float(args.get("duration", 0.0)))
        return ToolResult(output=f"slept {args.get('duration')}s")


@pytest.mark.asyncio
async def test_tool_timeout_returns_error_result_on_overrun() -> None:
    """A 5s sleep with ``tool_timeout_s=0.2`` returns a timeout-tagged error."""
    tool = _SleepTool()
    ctx = Context(system="")
    tc = ToolCall(id="call-1", name="sleeper", arguments={"duration": 5.0})
    config = LoopConfig(
        permissions=AutoApprove(),
        tool_timeout_s=0.2,
    )

    result = await async_execute_tool_calls_incremental(
        [tc], {tool.name: tool}, ctx, env=None, config=config,
    )

    assert len(result.results) == 1
    tr = result.results[0]
    assert not tr.success, f"expected error result on timeout, got: {tr}"
    assert tr.error is not None
    assert "sleeper" in tr.error and "timeout" in tr.error.lower(), tr.error


@pytest.mark.asyncio
async def test_tool_timeout_unset_runs_to_completion() -> None:
    """Without ``tool_timeout_s`` a brief sleep runs and returns success."""
    tool = _SleepTool()
    ctx = Context(system="")
    tc = ToolCall(id="call-2", name="sleeper", arguments={"duration": 0.05})
    config = LoopConfig(permissions=AutoApprove(), tool_timeout_s=None)

    result = await async_execute_tool_calls_incremental(
        [tc], {tool.name: tool}, ctx, env=None, config=config,
    )

    assert len(result.results) == 1
    tr = result.results[0]
    assert tr.success, f"expected success when no timeout set, got: {tr}"
    assert "slept" in tr.output


def test_cli_accepts_tool_timeout_flag() -> None:
    """``chimera mink --help`` advertises ``--tool-timeout``; ``--help`` exits 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "mink", "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--tool-timeout" in proc.stdout, proc.stdout

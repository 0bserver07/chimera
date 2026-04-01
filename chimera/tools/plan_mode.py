"""Plan mode tools — enter and exit plan mode.

When plan mode is active the agent can read and search but cannot write
files or run commands.  Use :class:`EnterPlanModeTool` to activate and
:class:`ExitPlanModeTool` to deactivate.
"""
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class EnterPlanModeTool(BaseTool):
    """Enter plan mode — agent can read and search but cannot write files or run commands."""

    name = "enter_plan_mode"
    description = (
        "Enter plan mode \u2014 agent can read and search but cannot write "
        "files or run commands"
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    is_read_only = True
    is_concurrency_safe = True

    def __init__(self) -> None:
        self._active = False

    @property
    def is_plan_mode_active(self) -> bool:
        return self._active

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        self._active = True
        return ToolResult(
            output=(
                "Plan mode activated. File writes and command execution are "
                "now blocked. Use exit_plan_mode when ready to execute."
            ),
        )

    async def async_execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return self.execute(args, env)


class ExitPlanModeTool(BaseTool):
    """Exit plan mode — resume normal execution with file writes and commands enabled."""

    name = "exit_plan_mode"
    description = (
        "Exit plan mode \u2014 resume normal execution with file writes "
        "and commands enabled"
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    is_read_only = True
    is_concurrency_safe = True

    def __init__(self, enter_tool: EnterPlanModeTool) -> None:
        self._enter_tool = enter_tool

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        self._enter_tool._active = False
        return ToolResult(output="Plan mode deactivated. Normal execution resumed.")

    async def async_execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return self.execute(args, env)

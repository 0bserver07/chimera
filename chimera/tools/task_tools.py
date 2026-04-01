"""Task management tools for interacting with background agent tasks.

Provides :class:`TaskOutputTool`, :class:`TaskStopTool`, and
:class:`TaskListTool` for reading output, stopping, and listing
background tasks managed by a :class:`~chimera.core.task_manager.TaskManager`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.core.task_manager import TaskManager


class TaskOutputTool(BaseTool):
    """Read output from a background task."""

    name = "task_output"
    description = "Read output from a background task"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
        },
        "required": ["task_id"],
    }
    is_read_only = True
    is_concurrency_safe = True

    def __init__(self, task_manager: TaskManager | None = None) -> None:
        self._tm = task_manager

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        if not self._tm:
            return ToolResult(output="", error="No task manager")
        task = self._tm.get(args["task_id"])
        if not task:
            return ToolResult(output="", error=f"Task {args['task_id']} not found")
        output = self._tm.read_output(args["task_id"])
        return ToolResult(output=output or f"Task status: {task.status}")


class TaskStopTool(BaseTool):
    """Stop a background task."""

    name = "task_stop"
    description = "Stop a background task"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
        },
        "required": ["task_id"],
    }

    def __init__(self, task_manager: TaskManager | None = None) -> None:
        self._tm = task_manager

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        if not self._tm:
            return ToolResult(output="", error="No task manager")
        self._tm.stop(args["task_id"])
        return ToolResult(output=f"Task {args['task_id']} stopped")


class TaskListTool(BaseTool):
    """List all background tasks."""

    name = "task_list"
    description = "List all background tasks"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }
    is_read_only = True
    is_concurrency_safe = True

    def __init__(self, task_manager: TaskManager | None = None) -> None:
        self._tm = task_manager

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        if not self._tm:
            return ToolResult(output="No tasks")
        tasks = self._tm.list_tasks()
        lines = [
            f"- {t.task_id}: {t.description} ({t.status})" for t in tasks
        ]
        return ToolResult(output="\n".join(lines) if lines else "No active tasks")

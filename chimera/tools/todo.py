# chimera/tools/todo.py
"""Agent-managed task list tool.

Provides a simple in-memory task list that agents can use to track
multi-step work.  Supports adding tasks, marking them complete, and
listing all tasks with their statuses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


@dataclass
class TodoItem:
    """A single task on the agent's to-do list.

    Attributes:
        id: Unique numeric identifier.
        task: Human-readable task description.
        done: Whether the task has been completed.
    """

    id: int
    task: str
    done: bool = False


class TodoTool(BaseTool):
    """Manage a task list to track multi-step work.

    Actions:
        add: Create a new task (requires ``task`` description).
        complete: Mark a task as done (requires ``task`` set to the task ID).
        list: Show all tasks with their statuses.
    """

    name = "todo"
    description = "Manage a task list to track multi-step work. Actions: add, complete, list."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "complete", "list"],
                "description": "Action to perform",
            },
            "task": {
                "type": "string",
                "description": "Task description (for 'add') or task ID (for 'complete')",
            },
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        self._items: list[TodoItem] = []
        self._next_id: int = 1

    def execute(self, args: dict[str, Any], env: Environment | None = None) -> ToolResult:
        """Execute a todo action.

        Args:
            args: Must contain ``action``.  For ``add``, also requires
                ``task`` (description).  For ``complete``, ``task`` should
                be the numeric task ID as a string.
            env: Unused — the task list is stored in memory.

        Returns:
            ToolResult with the action outcome or an error message.
        """
        action = args["action"]

        if action == "add":
            task = args.get("task", "")
            if not task:
                return ToolResult(output="", error="Task description required for 'add'")
            item = TodoItem(id=self._next_id, task=task)
            self._items.append(item)
            self._next_id += 1
            return ToolResult(output=f"Added task #{item.id}: {task}")

        elif action == "complete":
            task_id_str = args.get("task", "")
            try:
                task_id = int(task_id_str)
            except (ValueError, TypeError):
                return ToolResult(output="", error=f"Invalid task ID: {task_id_str}")
            for item in self._items:
                if item.id == task_id:
                    item.done = True
                    return ToolResult(output=f"Completed task #{task_id}: {item.task}")
            return ToolResult(output="", error=f"Task #{task_id} not found")

        elif action == "list":
            if not self._items:
                return ToolResult(output="No tasks.")
            lines = []
            for item in self._items:
                status = "done" if item.done else "todo"
                lines.append(f"#{item.id} [{status}] {item.task}")
            return ToolResult(output="\n".join(lines))

        else:
            return ToolResult(output="", error=f"Unknown action: {action}")

    @property
    def items(self) -> list[TodoItem]:
        """Return a shallow copy of the internal task list."""
        return list(self._items)

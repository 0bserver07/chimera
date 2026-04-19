"""TaskManager: lifecycle management for background agent tasks.

Provides :class:`BackgroundTask` to track individual task state and
:class:`TaskManager` to register, query, stop, and read output from
background tasks spawned by sub-agents.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.hooks.emitter import HookEmitter

__all__ = ["BackgroundTask", "TaskManager"]


@dataclass
class BackgroundTask:
    """Tracks the state of a single background agent task.

    Attributes:
        task_id: Unique identifier for this task.
        agent_id: The agent that owns this task.
        description: Human-readable description of what the task does.
        status: Current status (``"running"``, ``"completed"``, ``"stopped"``).
        output_path: Optional filesystem path where the task writes output.
        started_at: Unix timestamp when the task was registered.
        completed_at: Unix timestamp when the task finished, or ``None``.
    """

    task_id: str
    agent_id: str
    description: str
    status: str = "running"
    output_path: Path | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None


class TaskManager:
    """Manages the lifecycle of background agent tasks.

    Provides registration, lookup, listing, stopping, completion,
    and output reading for :class:`BackgroundTask` instances.
    """

    def __init__(self, emitter: HookEmitter | None = None) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._emitter = emitter

    def register(
        self,
        *,
        agent_id: str,
        description: str,
        output_path: Path | None = None,
    ) -> BackgroundTask:
        """Register a new background task and return it.

        Args:
            agent_id: The owning agent's identifier.
            description: Human-readable task description.
            output_path: Optional path for task output file.

        Returns:
            The newly created :class:`BackgroundTask`.
        """
        task_id = str(uuid.uuid4())
        task = BackgroundTask(
            task_id=task_id,
            agent_id=agent_id,
            description=description,
            status="running",
            output_path=output_path,
        )
        self._tasks[task_id] = task

        if self._emitter:
            from chimera.hooks.events import HookEvent

            emitter = self._emitter
            asyncio.get_event_loop().call_soon(
                lambda: asyncio.create_task(
                    emitter.emit(HookEvent.TASK_CREATED, tool_name=task.description)
                )
            )

        return task

    def get(self, task_id: str) -> BackgroundTask | None:
        """Return the task with *task_id*, or ``None`` if not found."""
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        """Return all registered tasks."""
        return list(self._tasks.values())

    def stop(self, task_id: str) -> None:
        """Mark a task as stopped. No-op if *task_id* is unknown."""
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.status = "stopped"
        task.completed_at = time.time()

    def complete(self, task_id: str) -> None:
        """Mark a task as completed. No-op if *task_id* is unknown."""
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.status = "completed"
        task.completed_at = time.time()

        if self._emitter:
            from chimera.hooks.events import HookEvent

            emitter = self._emitter
            asyncio.get_event_loop().call_soon(
                lambda: asyncio.create_task(
                    emitter.emit(HookEvent.TASK_COMPLETED, tool_name=task.description)
                )
            )

    def read_output(self, task_id: str) -> str | None:
        """Read the output file for the given task.

        Returns:
            The file contents as a string, or ``None`` if the task
            doesn't exist, has no output path, or the file is missing.
        """
        task = self._tasks.get(task_id)
        if task is None or task.output_path is None:
            return None
        try:
            return task.output_path.read_text()
        except (FileNotFoundError, OSError):
            return None

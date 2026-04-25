# chimera/tools/todo.py
"""Agent-managed task list tool with file-backed persistence.

Provides a simple task list that agents can use to track multi-step work.
State is mirrored to disk after every mutation so ``/resume`` can rehydrate
it on the next session start (CC-parity behavior).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.events.base import Event, EventBus


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


def _project_hash(cwd: str) -> str:
    """Return a stable hash for *cwd* so each project gets its own home file."""
    return hashlib.sha256(os.path.abspath(cwd).encode("utf-8")).hexdigest()[:16]


def _project_todo_path(cwd: str) -> Path:
    return Path(cwd) / ".chimera" / "todo.json"


def _user_todo_path(cwd: str) -> Path:
    return Path.home() / ".chimera" / "projects" / _project_hash(cwd) / "todo.json"


class TodoTool(BaseTool):
    """Manage a persistent task list to track multi-step work.

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

    def __init__(
        self,
        cwd: str | None = None,
        persist: bool = False,
        event_bus: EventBus | None = None,
        session_id: str = "",
    ) -> None:
        """Initialize the TodoTool.

        Args:
            cwd: Working directory the todo state is scoped to. Defaults to
                ``os.getcwd()`` at construction time.
            persist: When True, mutations are mirrored to disk under
                ``<cwd>/.chimera/todo.json`` (with a ``~/.chimera`` mirror).
                Defaults to False so bare ``TodoTool()`` instances stay
                fully ephemeral.
                # WHY: production callers (CLI, EventSourcedSession,
                # walking-skeleton example) explicitly pass persist=True
                # for CC-parity /resume behavior. Defaulting True instead
                # leaks state across pytest runs via the user-scope
                # mirror at ~/.chimera/projects/<sha256(cwd)>/todo.json,
                # because tests construct bare TodoTool() with no cwd.
            event_bus: Optional :class:`EventBus` to publish
                :class:`TodoWriteEvent` on every mutation.  Sessions wire
                this up so the EventLog can replay todos on /resume.
            session_id: Stamped onto every emitted ``TodoWriteEvent`` so
                multi-session logs can be filtered correctly.
        """
        self._items: list[TodoItem] = []
        self._next_id: int = 1
        self._cwd: str = os.path.abspath(cwd or os.getcwd())
        self._persist_enabled: bool = persist
        self._event_bus: EventBus | None = event_bus
        self._session_id: str = session_id
        if persist:
            self._load()

    def attach_event_bus(self, event_bus: EventBus, session_id: str = "") -> None:
        """Wire an :class:`EventBus` after construction.

        The :class:`EventSourcedSession` calls this once it has
        instantiated its log so all subsequent mutations produce durable
        :class:`TodoWriteEvent` records.

        Args:
            event_bus: The bus to publish events on.
            session_id: Session identifier stamped onto every event.
        """
        self._event_bus = event_bus
        if session_id:
            self._session_id = session_id

    def execute(self, args: dict[str, Any], env: Environment | None = None) -> ToolResult:
        """Execute a todo action.

        Args:
            args: Must contain ``action``.  For ``add``, also requires
                ``task`` (description).  For ``complete``, ``task`` should
                be the numeric task ID as a string.
            env: Unused — the task list is stored in memory and (optionally)
                a JSON file alongside the project.

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
            self._persist()
            # WHY: M3-C handles file persistence; this emit makes the same
            # mutation durable on the EventLog so /resume can rebuild the
            # list even when the JSON mirror is unavailable (read-only cwd,
            # different host, etc.).
            self._emit_write("add")
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
                    self._persist()
                    self._emit_write("complete")
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

    # ------------------------------------------------------------------
    # Event sourcing (M4-D)
    # ------------------------------------------------------------------

    def _emit_write(self, op: str) -> None:
        """Publish a :class:`TodoWriteEvent` for the current state.

        Silently noops when no event bus is attached so unit tests and
        ad-hoc TodoTool instances stay zero-config.

        Args:
            op: The mutation kind — ``"add"``, ``"complete"``, ``"set"``,
                or ``"remove"``.
        """
        if self._event_bus is None:
            return
        # Local import keeps chimera.tools free of an events.types import
        # at module load time and avoids any circular-import surprises.
        from chimera.events.types import TodoWriteEvent

        snapshot = [asdict(it) for it in self._items]
        self._event_bus.publish(
            TodoWriteEvent(
                todos=snapshot,
                op=op,
                session_id=self._session_id,
            )
        )

    def apply_event(self, event: Event) -> None:
        """Apply a replayed :class:`TodoWriteEvent` to in-memory state.

        Each event's ``todos`` field is the *post-mutation* snapshot, so
        replay is just a series of full-state restores in log order — the
        final event wins.  Indexing is preserved by reading the ``id``
        from the snapshot rather than re-numbering.

        Args:
            event: A previously emitted ``TodoWriteEvent`` (or its
                serialized base-:class:`Event` form, where ``todos`` and
                ``op`` live in ``metadata``).
        """
        # The EventLog deserializes everything as a base Event with the
        # payload in metadata; the runtime path delivers a TodoWriteEvent
        # whose fields are first-class attributes. Handle both.
        todos = getattr(event, "todos", None)
        if todos is None:
            todos = event.metadata.get("todos", [])
        # Defensive: don't trust replayed data blindly.
        new_items: list[TodoItem] = []
        for raw in todos:
            if not isinstance(raw, dict) or "id" not in raw or "task" not in raw:
                continue
            new_items.append(
                TodoItem(
                    id=int(raw["id"]),
                    task=str(raw["task"]),
                    done=bool(raw.get("done", False)),
                )
            )
        self._items = new_items
        self._next_id = max((it.id for it in self._items), default=0) + 1

    @classmethod
    def load_at_session_start(
        cls, session_id: str, cwd: str,
    ) -> TodoTool:
        """Construct a TodoTool whose state is rehydrated from disk.

        Used by ``/resume`` so a long-running todo list survives across
        sessions.  ``session_id`` is currently unused (state is keyed by
        cwd, not session) but accepted to match the planned signature.

        Args:
            session_id: Session identifier (reserved for future per-session
                storage; today the project-scoped file is authoritative).
            cwd: Working directory whose project-scoped todo file should
                be loaded.

        Returns:
            A new TodoTool instance with persisted items already loaded.
        """
        del session_id  # intentionally unused; reserved.
        return cls(cwd=cwd, persist=True)

    # ------------------------------------------------------------------
    # Persistence (private)
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Restore items from the most authoritative on-disk file.

        The project-scoped file (``<cwd>/.chimera/todo.json``) wins when
        both exist; the user-scoped mirror is the fallback for read-only
        cwds. Missing or corrupt files are treated as empty (best-effort
        recovery — never raise during agent startup).
        """
        for path in (_project_todo_path(self._cwd), _user_todo_path(self._cwd)):
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            items = data.get("items", []) if isinstance(data, dict) else []
            self._items = [
                TodoItem(id=int(it["id"]), task=str(it["task"]), done=bool(it.get("done", False)))
                for it in items
                if isinstance(it, dict) and "id" in it and "task" in it
            ]
            self._next_id = max((it.id for it in self._items), default=0) + 1
            return

    def _persist(self) -> None:
        """Atomically write current state to both project and user files.

        Atomicity is required because a crash mid-write would leave an
        agent unable to /resume; tempfile + rename guarantees readers
        only ever see complete JSON.
        """
        if not self._persist_enabled:
            return
        payload = {"items": [asdict(it) for it in self._items]}
        body = json.dumps(payload, indent=2)
        for target in (_project_todo_path(self._cwd), _user_todo_path(self._cwd)):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(
                    prefix=".todo.", suffix=".tmp", dir=str(target.parent),
                )
                with os.fdopen(fd, "w") as fh:
                    fh.write(body)
                os.replace(tmp, target)
            except OSError:
                # Best-effort: a missing user-home (e.g. CI) shouldn't break
                # agent execution. Project-scope success is enough.
                continue

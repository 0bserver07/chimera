"""Task tool — spawn child agents with isolated context.

See ``docs/mink/subagents.md`` for the full schema, isolation tiers
(``full`` | ``selective`` | ``shared``), foreground/background semantics,
and cancellation cascade rules.
"""
from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chimera.core.cancellation import CancellationToken
from chimera.core.context import Context
from chimera.core.file_tracker import FileTracker
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.permissions.presets import AllowList, AutoApprove
from chimera.types import AgentResult, Message, ToolResult
from chimera.config.paths import store_path

if TYPE_CHECKING:
    from chimera.agents.loader import AgentLoader
    from chimera.core.agent import Agent
    from chimera.providers.base import Provider


__all__ = [
    "TaskTool", "TaskManager", "TaskRecord",
    "TaskListTool", "TaskGetTool", "TaskOutputTool", "TaskStopTool",
    "TaskNotFinished", "TaskNotFound", "ISOLATION_TIERS",
]

DEFAULT_TASK_DIR = store_path("tasks")
ISOLATION_TIERS = ("full", "selective", "shared")


class TaskNotFound(KeyError):
    """Raised when an agent_id is not present in :class:`TaskManager`."""


class TaskNotFinished(RuntimeError):
    """Raised when ``output()`` is called for a still-running task."""


@dataclass
class TaskRecord:
    """In-memory record for a background task."""

    agent_id: str
    description: str
    subagent_type: str
    status: str
    output_path: Path
    cancellation: CancellationToken
    started_at: float = field(default_factory=time.time)
    thread: threading.Thread | None = None
    result: AgentResult | None = None
    error: str | None = None


class TaskManager:
    """Thread-safe registry of in-flight and completed background tasks."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()
        self._output_dir = output_dir or DEFAULT_TASK_DIR
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def register(self, record: TaskRecord) -> None:
        """Add a record under its ``agent_id``."""
        with self._lock:
            self._tasks[record.agent_id] = record

    def update_status(
        self, agent_id: str, status: str,
        result: AgentResult | None = None, error: str | None = None,
    ) -> None:
        """Mark a task with a terminal status and persist its output file."""
        with self._lock:
            rec = self._tasks.get(agent_id)
            if rec is None:
                return
            rec.status = status
            rec.result = result
            rec.error = error
        self._write_output(agent_id)

    def list(self) -> list[TaskRecord]:
        with self._lock:
            return list(self._tasks.values())

    def get(self, agent_id: str) -> TaskRecord:
        with self._lock:
            rec = self._tasks.get(agent_id)
        if rec is None:
            raise TaskNotFound(agent_id)
        return rec

    def output(self, agent_id: str) -> dict[str, Any]:
        """Return the JSON-serialised result for a finished task.

        Raises:
            TaskNotFound: If ``agent_id`` was never registered.
            TaskNotFinished: If the task is still running.
        """
        rec = self.get(agent_id)
        if rec.status == "running":
            raise TaskNotFinished(agent_id)
        if rec.output_path.exists():
            data: dict[str, Any] = json.loads(rec.output_path.read_text())
            return data
        return _record_to_dict(rec)

    def wait(self, agent_id: str, timeout: float | None = None) -> TaskRecord:
        rec = self.get(agent_id)
        if rec.thread is not None:
            rec.thread.join(timeout=timeout)
        return rec

    def cancel(self, agent_id: str) -> None:
        rec = self.get(agent_id)
        rec.cancellation.cancel()

    def _write_output(self, agent_id: str) -> None:
        rec = self._tasks.get(agent_id)
        if rec is None:
            return
        try:
            rec.output_path.parent.mkdir(parents=True, exist_ok=True)
            rec.output_path.write_text(json.dumps(_record_to_dict(rec), indent=2))
        except OSError:
            pass


def _record_to_dict(rec: TaskRecord) -> dict[str, Any]:
    """Serialise a :class:`TaskRecord` to a JSON-safe dict."""
    out: dict[str, Any] = {
        "agent_id": rec.agent_id, "description": rec.description,
        "subagent_type": rec.subagent_type, "status": rec.status,
        "started_at": rec.started_at, "error": rec.error,
    }
    if rec.result is not None:
        out["result"] = dataclasses.asdict(rec.result)
    return out


def _create_child_context(
    parent: Agent,
    isolation: str,
    allowed_tools: list[str] | None,
    parent_cancel: CancellationToken | None = None,
    parent_file_tracker: FileTracker | None = None,
    max_steps: int = 50,
) -> tuple[ReAct, list[BaseTool], Context, CancellationToken, FileTracker]:
    """Build ``(loop, tools, context, cancel_token, file_tracker)`` for a child run.

    See :data:`ISOLATION_TIERS` and ``docs/mink/subagents.md`` for tier
    semantics. Raises :class:`ValueError` if ``isolation`` is unknown.
    """
    if isolation not in ISOLATION_TIERS:
        raise ValueError(f"isolation must be one of {ISOLATION_TIERS!r}; got {isolation!r}")

    if allowed_tools is not None:
        whitelist = set(allowed_tools)
        child_tools = [t for t in parent.tools if t.name in whitelist]
    else:
        child_tools = list(parent.tools)

    child_cancel = CancellationToken()
    if parent_cancel is not None:
        # Parent cancel cascades to child; child cancel never bubbles up.
        parent_cancel.on_cancel(child_cancel.cancel)

    if isolation == "shared" and parent_file_tracker is not None:
        child_tracker = parent_file_tracker
    elif parent_file_tracker is not None:
        child_tracker = FileTracker(
            read_files=list(parent_file_tracker.read_files),
            modified_files=list(parent_file_tracker.modified_files),
        )
        child_tracker._seen_read = set(parent_file_tracker._seen_read)
        child_tracker._seen_modified = set(parent_file_tracker._seen_modified)
    else:
        child_tracker = FileTracker()

    permissions = AllowList(list(allowed_tools)) if allowed_tools else AutoApprove()
    system_prompt = parent.prompt.render(tools=[t.name for t in child_tools])
    context = Context(system=system_prompt)
    config = LoopConfig(
        permissions=permissions, cancellation=child_cancel, file_tracker=child_tracker,
    )
    loop = ReAct(max_steps=max_steps, config=config)
    return loop, child_tools, context, child_cancel, child_tracker


class TaskTool(BaseTool):
    """Spawn a subagent with isolated context.

    Bound to a parent :class:`Agent` (constructor or :meth:`bind_parent`)
    so the subagent inherits its provider and tool surface.  The
    ``subagent_type`` argument resolves through :class:`AgentLoader`
    against ``.claude/agents`` (project), ``~/.claude/agents`` (user),
    and the built-in registry.
    """

    name = "Task"
    description = (
        "Spawn a subagent with an isolated context. The child runs in its "
        "own loop with a fresh message history. Set run_in_background=True "
        "for fire-and-forget; otherwise the call blocks and returns the "
        "child's final output."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Short task summary."},
            "prompt": {"type": "string", "description": "Detailed task prompt."},
            "subagent_type": {"type": "string", "description": "Subagent definition name."},
            "isolation": {"type": "string", "enum": list(ISOLATION_TIERS), "default": "full"},
            "run_in_background": {"type": "boolean", "default": False},
            "model": {"type": "string", "description": "Optional model override."},
            "allowed_tools": {
                "type": "array", "items": {"type": "string"},
                "description": "Whitelist of tool names the child may use.",
            },
            "name": {"type": "string", "description": "Optional task display name."},
        },
        "required": ["description", "prompt", "subagent_type"],
    }
    is_concurrency_safe = False

    def __init__(
        self,
        parent: Agent | None = None,
        agent_loader: AgentLoader | None = None,
        task_manager: TaskManager | None = None,
        provider: Provider | None = None,
    ) -> None:
        """Create a TaskTool. ``parent`` may be wired post-init via :meth:`bind_parent`."""
        self._parent = parent
        self._agent_loader = agent_loader
        self._task_manager = task_manager or TaskManager()
        self._provider_override = provider

    def bind_parent(self, parent: Agent) -> None:
        self._parent = parent

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Resolve the subagent and dispatch it (foreground or background)."""
        if self._parent is None:
            return ToolResult(output="", error="TaskTool has no parent agent bound")
        try:
            description = str(args["description"])
            prompt = str(args["prompt"])
            subagent_type = str(args["subagent_type"])
        except KeyError as exc:
            return ToolResult(output="", error=f"Missing required arg: {exc.args[0]}")

        isolation = args.get("isolation", "full")
        run_in_background = bool(args.get("run_in_background", False))
        allowed_tools = args.get("allowed_tools")
        if allowed_tools is not None and not isinstance(allowed_tools, list):
            return ToolResult(output="", error="allowed_tools must be a list of strings")

        agent_def = self._resolve_subagent(subagent_type)
        parent_max = getattr(self._parent.loop, "max_steps", 50)
        max_steps = min(
            agent_def.max_iterations if agent_def is not None else parent_max, parent_max,
        )
        cfg = self._parent.loop.config
        parent_cancel = cfg.cancellation if cfg is not None else None
        parent_tracker = cfg.file_tracker if cfg is not None else None

        loop, child_tools, context, child_cancel, _ = _create_child_context(
            parent=self._parent,
            isolation=isolation,
            allowed_tools=allowed_tools,
            parent_cancel=parent_cancel,
            parent_file_tracker=parent_tracker,
            max_steps=max_steps,
        )

        # Subagent system prompt overrides parent's prompt when present.
        if agent_def is not None and agent_def.system_prompt:
            context = Context(system=agent_def.system_prompt)
        context.add(Message.user(prompt))

        provider = self._provider_override or self._parent.provider
        agent_id = f"task_{uuid.uuid4().hex[:12]}"

        if run_in_background:
            return self._spawn_background(
                agent_id=agent_id, description=description,
                subagent_type=subagent_type, provider=provider,
                loop=loop, tools=child_tools, context=context,
                env=env, cancel=child_cancel,
            )
        return self._spawn_foreground(
            provider=provider, loop=loop, tools=child_tools,
            context=context, env=env, agent_id=agent_id,
        )

    def _spawn_foreground(
        self, provider: Provider, loop: ReAct, tools: list[BaseTool],
        context: Context, env: Environment | None, agent_id: str,
    ) -> ToolResult:
        try:
            result = loop.run(provider, tools, context, env)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(output="", error=f"Subagent error: {exc}")
        if result.success:
            return ToolResult(
                output=result.output,
                metadata={"agent_id": agent_id, "steps": result.steps},
            )
        return ToolResult(output=result.output, error=result.error)

    def _spawn_background(
        self, agent_id: str, description: str, subagent_type: str,
        provider: Provider, loop: ReAct, tools: list[BaseTool],
        context: Context, env: Environment | None, cancel: CancellationToken,
    ) -> ToolResult:
        output_path = self._task_manager._output_dir / f"{agent_id}.output"
        record = TaskRecord(
            agent_id=agent_id, description=description,
            subagent_type=subagent_type, status="running",
            output_path=output_path, cancellation=cancel,
        )

        def _run() -> None:
            try:
                result = loop.run(provider, tools, context, env)
                self._task_manager.update_status(
                    agent_id,
                    status="completed" if result.success else "failed",
                    result=result, error=result.error,
                )
            except Exception as exc:  # noqa: BLE001
                self._task_manager.update_status(
                    agent_id, status="failed", error=str(exc),
                )

        thread = threading.Thread(
            target=_run, name=f"chimera-task-{agent_id}", daemon=True,
        )
        record.thread = thread
        self._task_manager.register(record)
        thread.start()
        return ToolResult(
            output=json.dumps({"agent_id": agent_id, "status": "running"}),
            metadata={"agent_id": agent_id, "background": True},
        )

    def _resolve_subagent(self, name: str) -> Any:
        if self._agent_loader is None:
            from chimera.agents.loader import AgentLoader as _AL
            self._agent_loader = _AL(project_root=os.getcwd())
        return self._agent_loader.get(name)


# ---------------------------------------------------------------------------
# Companion tools — operate on a shared TaskManager.
# ---------------------------------------------------------------------------


_ID_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {"agent_id": {"type": "string"}},
    "required": ["agent_id"],
}


class _TaskCompanion(BaseTool):
    """Common base for ``task_*`` companion tools."""

    is_concurrency_safe = True

    def __init__(self, task_manager: TaskManager) -> None:
        self._tm = task_manager


class TaskListTool(_TaskCompanion):
    """List every known background task."""
    name = "task_list"
    description = "List all known background tasks (id, description, status)."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    is_read_only = True
    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        tasks = self._tm.list()
        if not tasks:
            return ToolResult(output="No tasks")
        return ToolResult(output="\n".join(
            f"- {t.agent_id}: {t.description} [{t.status}]" for t in tasks
        ))


class TaskGetTool(_TaskCompanion):
    """Inspect a single task's status and metadata."""
    name = "task_get"
    description = "Return status and metadata for a single background task."
    parameters = _ID_PARAMS
    is_read_only = True
    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        try:
            rec = self._tm.get(args["agent_id"])
        except TaskNotFound as exc:
            return ToolResult(output="", error=f"Task not found: {exc.args[0]}")
        return ToolResult(output=json.dumps(_record_to_dict(rec), indent=2))


class TaskOutputTool(_TaskCompanion):
    """Read the final output of a finished background task."""
    name = "task_output"
    description = "Return the final result JSON of a finished background task."
    parameters = _ID_PARAMS
    is_read_only = True
    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        try:
            data = self._tm.output(args["agent_id"])
        except TaskNotFound as exc:
            return ToolResult(output="", error=f"Task not found: {exc.args[0]}")
        except TaskNotFinished:
            return ToolResult(output="", error="Task is still running")
        return ToolResult(output=json.dumps(data, indent=2))


class TaskStopTool(_TaskCompanion):
    """Cancel a running background task."""
    name = "task_stop"
    description = "Signal cancellation for a running background task."
    parameters = _ID_PARAMS
    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        try:
            self._tm.cancel(args["agent_id"])
        except TaskNotFound as exc:
            return ToolResult(output="", error=f"Task not found: {exc.args[0]}")
        return ToolResult(output=f"Cancellation requested for {args['agent_id']}")

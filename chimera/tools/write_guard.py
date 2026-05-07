"""WriteGuard — pre-execution invariant for ``write_file`` vs ``edit_file``.

Background: across the polyglot benchmark suite, agents pick the wrong
mutation tool roughly 57% of the time — ``write_file`` is invoked
against an existing file (clobbering it instead of patching), or
``edit_file`` is invoked against a non-existent file (which then
fails with a confusing "string not found" error). WriteGuard tightens
the invariant by checking the target path's existence *before* the
write actually lands, so the agent gets a focused suggestion to use
the other tool instead.

Surface:

* :class:`WriteGuardError` — raised by ``check_write`` / ``check_edit``.
* :class:`WriteGuard` — process-wide on/off switch and the pair of
  static check methods used by :class:`~chimera.tools.write.WriteFileTool`
  and :class:`~chimera.tools.edit.EditFileTool` when enforcement is on.
* :class:`WriteGuardTool` — agent-facing tool exposing ``check_write``,
  ``check_edit``, ``enable``, ``disable``, ``status`` so the agent can
  pre-flight a path and toggle enforcement at runtime.

Default state is *disabled* so the guard never silently changes
behaviour for callers who haven't opted in. A consumer flips the
guard via :meth:`WriteGuard.set_enforced` (e.g. from a CLI flag, a
benchmark runner, or via the ``write_guard`` tool itself).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class WriteGuardError(Exception):
    """Raised when a write/edit invariant is violated.

    Carries both the offending tool name and a corrected suggestion so
    the message returned to the agent is unambiguous and short.
    """

    def __init__(self, tool: str, path: str, suggestion: str) -> None:
        self.tool = tool
        self.path = path
        self.suggestion = suggestion
        super().__init__(
            f"{tool} invariant violated for '{path}': {suggestion}"
        )


class WriteGuard:
    """Process-wide guard for write/edit invariants.

    The guard is *off* by default. Callers opt in via
    :meth:`set_enforced`. When enforced, :class:`WriteFileTool` and
    :class:`EditFileTool` consult :meth:`check_write` and
    :meth:`check_edit` before touching the filesystem.
    """

    _enforced: ClassVar[bool] = False

    @classmethod
    def is_enforced(cls) -> bool:
        """Return ``True`` if the guard is currently enforcing invariants."""
        return cls._enforced

    @classmethod
    def set_enforced(cls, enabled: bool) -> None:
        """Enable or disable enforcement process-wide.

        Args:
            enabled: When ``True``, subsequent ``write_file`` and
                ``edit_file`` calls run through :meth:`check_write` /
                :meth:`check_edit` before mutating the filesystem.
        """
        cls._enforced = bool(enabled)

    @classmethod
    def reset(cls) -> None:
        """Reset enforcement to the default-disabled state.

        Test fixtures use this between cases so guard state from one
        test never leaks into the next.
        """
        cls._enforced = False

    @staticmethod
    def _resolve(path: str, env: Environment | None) -> str:
        """Map a relative ``path`` into an absolute filesystem path.

        Uses the environment's ``cwd`` / ``workdir`` when available so
        the check honors a sandboxed environment's notion of "the
        current directory" rather than the host process's.
        """
        if not path:
            return path
        if os.path.isabs(path):
            return path
        if env is not None:
            cwd = getattr(env, "cwd", None) or getattr(env, "workdir", None)
            if cwd:
                return os.path.join(str(cwd), path)
        return str(Path(path).resolve())

    @classmethod
    def check_write(cls, path: str, env: Environment | None = None) -> None:
        """Raise :class:`WriteGuardError` if ``write_file`` would clobber an existing file.

        ``write_file`` semantically *creates* a file. If the target
        already exists, the agent almost always meant to use
        ``edit_file`` (or ``apply_patch``) to modify it.
        """
        if not path:
            return
        full = cls._resolve(path, env)
        if Path(full).exists():
            raise WriteGuardError(
                tool="write_file",
                path=path,
                suggestion=(
                    f"file already exists; use 'edit_file' or 'apply_patch' "
                    f"to modify '{path}' instead of overwriting it with "
                    f"write_file"
                ),
            )

    @classmethod
    def check_edit(cls, path: str, env: Environment | None = None) -> None:
        """Raise :class:`WriteGuardError` if ``edit_file`` would target a missing file.

        ``edit_file`` requires a file to patch. If the target does not
        exist, the agent meant to use ``write_file`` to create it.
        """
        if not path:
            return
        full = cls._resolve(path, env)
        if not Path(full).exists():
            raise WriteGuardError(
                tool="edit_file",
                path=path,
                suggestion=(
                    f"file does not exist; use 'write_file' to create "
                    f"'{path}' instead of editing it with edit_file"
                ),
            )


class WriteGuardTool(BaseTool):
    """Agent-facing surface for :class:`WriteGuard`.

    Lets the agent pre-flight a path against the guard or toggle
    enforcement. Useful when an agent wants to verify intent before
    issuing the actual ``write_file`` / ``edit_file`` call, and when a
    runner wants to enable the guard for the rest of the session.
    """

    name = "write_guard"
    description = (
        "Verify or toggle the write_file / edit_file invariant guard. "
        "Use action='check_write' or 'check_edit' with a 'path' to "
        "pre-flight a path; use action='enable' / 'disable' to flip "
        "process-wide enforcement; use action='status' to read the "
        "current state."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "check_write",
                    "check_edit",
                    "enable",
                    "disable",
                    "status",
                ],
                "description": "Operation to perform.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Filesystem path. Required for check_write / check_edit; "
                    "ignored for enable / disable / status."
                ),
            },
        },
        "required": ["action"],
    }
    is_read_only = True
    is_concurrency_safe = True

    _VALID_ACTIONS = ("check_write", "check_edit", "enable", "disable", "status")

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        action = args.get("action")
        if action not in self._VALID_ACTIONS:
            return ToolResult(
                output="",
                error=f"write_guard: unknown action {action!r}",
            )

        if action == "status":
            state = "enabled" if WriteGuard.is_enforced() else "disabled"
            return ToolResult(output=f"write_guard: {state}")
        if action == "enable":
            WriteGuard.set_enforced(True)
            return ToolResult(output="write_guard: enabled")
        if action == "disable":
            WriteGuard.set_enforced(False)
            return ToolResult(output="write_guard: disabled")

        path = args.get("path")
        if not isinstance(path, str) or not path:
            return ToolResult(
                output="",
                error=f"write_guard: 'path' is required for action={action!r}",
            )

        if action == "check_write":
            try:
                WriteGuard.check_write(path, env)
            except WriteGuardError as e:
                return ToolResult(output="", error=str(e))
            return ToolResult(output=f"write_guard: 'write_file {path}' is allowed")

        # action == "check_edit" — the only remaining branch in _VALID_ACTIONS.
        try:
            WriteGuard.check_edit(path, env)
        except WriteGuardError as e:
            return ToolResult(output="", error=str(e))
        return ToolResult(output=f"write_guard: 'edit_file {path}' is allowed")


__all__ = ["WriteGuard", "WriteGuardError", "WriteGuardTool"]

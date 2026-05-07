# chimera/tools/edit.py
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ChangeType, FileChange, ToolResult

if TYPE_CHECKING:
    from chimera.core.operations import ReadOps, WriteOps
    from chimera.tools.strategies import FuzzyEditor


class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Replace an exact string in a file with a new string. The old_string must appear exactly once."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "old_string": {"type": "string", "description": "Exact string to find (must be unique)"},
            "new_string": {"type": "string", "description": "Replacement string"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    # --- #130: Read-before-write tracking ---
    _read_files: ClassVar[set[str]] = set()
    _enforce_read_before_write: ClassVar[bool] = False

    @classmethod
    def mark_file_read(cls, path: str) -> None:
        """Record that *path* has been read in this session."""
        cls._read_files.add(str(Path(path).resolve()))

    @classmethod
    def was_file_read(cls, path: str) -> bool:
        """Return ``True`` if *path* was previously marked as read."""
        return str(Path(path).resolve()) in cls._read_files

    @classmethod
    def reset_read_tracking(cls) -> None:
        """Clear all read-tracking state."""
        cls._read_files.clear()

    @classmethod
    def set_enforce_read_before_write(cls, enabled: bool) -> None:
        """Enable or disable the read-before-write guard."""
        cls._enforce_read_before_write = enabled

    def __init__(
        self,
        editor: FuzzyEditor | None = None,
        read_ops: ReadOps | None = None,
        write_ops: WriteOps | None = None,
    ) -> None:
        self._editor = editor
        self._read_ops = read_ops
        self._write_ops = write_ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        path = args["path"]

        # WriteGuard (W13-G13): when enforced, refuse to edit_file a missing
        # file — the agent meant write_file. Runs before read-before-write so
        # missing-file diagnostics are not masked by the read-tracking check.
        from chimera.tools.write_guard import WriteGuard, WriteGuardError

        if WriteGuard.is_enforced():
            try:
                WriteGuard.check_edit(path, env)
            except WriteGuardError as e:
                return ToolResult(output="", error=str(e))

        # --- #130: Read-before-write guard ---
        if self._enforce_read_before_write:
            resolved = _resolve_edit_path(path, self._read_ops, env)
            if resolved and not self.was_file_read(resolved):
                return ToolResult(
                    output="",
                    error=f"You must read '{path}' before editing it. Use the read_file tool first.",
                )

        # Read content via ops or env
        if self._read_ops is not None:
            try:
                content = self._read_ops.read_file(path)
            except FileNotFoundError:
                return ToolResult(output="", error=f"File not found: {path}")
        else:
            assert env is not None
            try:
                content = env.read_file(path)
            except FileNotFoundError:
                return ToolResult(output="", error=f"File not found: {path}")

        old = args["old_string"]
        new = args["new_string"]
        count = content.count(old)

        if count == 1:
            # Exact match — existing behavior
            updated = content.replace(old, new, 1)
            match_strategy = "exact"
        elif self._editor is not None:
            # Try fuzzy strategies
            result = self._editor.find(content, old)
            if result is None:
                return ToolResult(output="", error=f"String not found in {path} (tried fuzzy matching)")
            updated = content[:result.start] + new + content[result.end:]
            match_strategy = result.strategy_name
        elif count == 0:
            return ToolResult(output="", error=f"String not found in {path}")
        else:
            return ToolResult(output="", error=f"Multiple matches ({count}) found — ambiguous. Provide more context.")

        # Write via ops or env
        if self._write_ops is not None:
            self._write_ops.write_file(path, updated)
        else:
            assert env is not None
            env.write_file(path, updated)

        fc = FileChange(
            path=path,
            change_type=ChangeType.EDIT,
            before_content=content,
            after_content=updated,
            diff=FileChange.compute_diff(path, content, updated),
        )
        return ToolResult(
            output=f"Edited {path}",
            metadata={"file_change": fc, "match_strategy": match_strategy},
        )


def _resolve_edit_path(
    path: str,
    read_ops: Any | None,
    env: Environment | None,
) -> str:
    """Resolve *path* to absolute using the ops or env cwd if available."""
    import os

    if not path:
        return ""
    if os.path.isabs(path):
        return path
    backend = read_ops or env
    if backend is not None:
        cwd = getattr(backend, "cwd", None) or getattr(backend, "workdir", None)
        if cwd:
            return os.path.join(cwd, path)
    return str(Path(path).resolve())

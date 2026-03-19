# chimera/tools/edit.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

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

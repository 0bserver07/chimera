# chimera/tools/edit.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ChangeType, FileChange, ToolResult


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

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        path = args["path"]
        try:
            content = env.read_file(path)
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {path}")

        old = args["old_string"]
        new = args["new_string"]
        count = content.count(old)

        if count == 0:
            return ToolResult(output="", error=f"String not found in {path}")
        if count > 1:
            return ToolResult(output="", error=f"Multiple matches ({count}) found — ambiguous. Provide more context.")

        updated = content.replace(old, new, 1)
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
            metadata={"file_change": fc},
        )

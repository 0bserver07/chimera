# chimera/tools/edit.py
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


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
        try:
            content = env.read_file(args["path"])
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {args['path']}")

        old = args["old_string"]
        new = args["new_string"]
        count = content.count(old)

        if count == 0:
            return ToolResult(output="", error=f"String not found in {args['path']}")
        if count > 1:
            return ToolResult(output="", error=f"Multiple matches ({count}) found — ambiguous. Provide more context.")

        updated = content.replace(old, new, 1)
        env.write_file(args["path"], updated)
        return ToolResult(output=f"Edited {args['path']}")

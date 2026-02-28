from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ChangeType, FileChange, ToolResult


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file. Creates parent directories if needed."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        path = args["path"]
        new_content = args["content"]

        # Read before-state for diff
        before: str | None = None
        try:
            before = env.read_file(path)
        except (FileNotFoundError, OSError):
            pass

        try:
            env.write_file(path, new_content)
        except Exception as e:
            return ToolResult(output="", error=str(e))

        if before is None:
            change_type = ChangeType.CREATE
        else:
            change_type = ChangeType.EDIT

        fc = FileChange(
            path=path,
            change_type=change_type,
            before_content=before,
            after_content=new_content,
            diff=FileChange.compute_diff(path, before or "", new_content),
        )
        return ToolResult(
            output=f"Written to {path}",
            metadata={"file_change": fc},
        )

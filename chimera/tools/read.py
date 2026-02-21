from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
        },
        "required": ["path"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        try:
            content = env.read_file(args["path"])
            return ToolResult(output=content)
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {args['path']}")

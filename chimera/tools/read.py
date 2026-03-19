from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.core.operations import ReadOps


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

    def __init__(self, ops: ReadOps | None = None) -> None:
        self._ops = ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        path = args["path"]
        if self._ops is not None:
            try:
                content = self._ops.read_file(path)
                return ToolResult(output=content)
            except FileNotFoundError:
                return ToolResult(output="", error=f"File not found: {path}")
        assert env is not None
        try:
            content = env.read_file(path)
            return ToolResult(output=content)
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {path}")

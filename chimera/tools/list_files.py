# chimera/tools/list_files.py
from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.core.operations import SearchOps


class ListFilesTool(BaseTool):
    name = "list_files"
    description = "List files in a directory, optionally filtered by glob pattern."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list", "default": "."},
            "glob": {"type": "string", "description": "Glob filter (e.g. '*.py')", "default": None},
        },
        "required": [],
    }

    def __init__(self, ops: SearchOps | None = None) -> None:
        self._ops = ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        path = args.get("path", ".")
        glob_filter = args.get("glob")

        if self._ops is not None:
            files = self._ops.list_files("**/*")
            if path != ".":
                files = [f for f in files if f.startswith(path + "/") or f.startswith(path)]
            if glob_filter:
                files = [f for f in files if fnmatch.fnmatch(f.split("/")[-1], glob_filter)]
            return ToolResult(output="\n".join(sorted(files)) if files else "No files found.")

        assert env is not None

        files = env.list_files("**/*")
        if path != ".":
            files = [f for f in files if f.startswith(path + "/") or f.startswith(path)]
        if glob_filter:
            files = [f for f in files if fnmatch.fnmatch(f.split("/")[-1], glob_filter)]

        return ToolResult(output="\n".join(sorted(files)) if files else "No files found.")

# chimera/tools/list_files.py
from __future__ import annotations

import fnmatch
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


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

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        path = args.get("path", ".")
        glob_filter = args.get("glob")

        files = env.list_files("**/*")
        if path != ".":
            files = [f for f in files if f.startswith(path + "/") or f.startswith(path)]
        if glob_filter:
            files = [f for f in files if fnmatch.fnmatch(f.split("/")[-1], glob_filter)]

        return ToolResult(output="\n".join(sorted(files)) if files else "No files found.")

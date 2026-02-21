# chimera/tools/replace_in_file.py
from __future__ import annotations

import re
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class ReplaceInFileTool(BaseTool):
    name = "replace_in_file"
    description = "Replace all occurrences of a regex pattern in a file."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to the file"},
            "pattern": {"type": "string", "description": "Regex pattern to match"},
            "replacement": {"type": "string", "description": "Replacement string (supports \\1 backreferences)"},
        },
        "required": ["path", "pattern", "replacement"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        try:
            content = env.read_file(args["path"])
        except FileNotFoundError:
            return ToolResult(output="", error=f"File not found: {args['path']}")

        try:
            updated, count = re.subn(args["pattern"], args["replacement"], content)
        except re.error as e:
            return ToolResult(output="", error=f"Invalid regex: {e}")

        if count == 0:
            return ToolResult(output=f"0 replacements made in {args['path']}")

        env.write_file(args["path"], updated)
        return ToolResult(output=f"{count} replacement(s) made in {args['path']}")

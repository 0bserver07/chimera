# chimera/tools/search.py
from __future__ import annotations

import fnmatch
import re
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class SearchTool(BaseTool):
    name = "search"
    description = "Search for a regex pattern across files. Returns matching lines with file paths and line numbers."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "File or directory to search in", "default": "."},
            "glob": {"type": "string", "description": "Glob filter for filenames (e.g. '*.py')", "default": None},
        },
        "required": ["pattern"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        assert env is not None
        pattern = args["pattern"]
        search_path = args.get("path", ".")
        glob_filter = args.get("glob")

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(output="", error=f"Invalid regex: {e}")

        # Get files to search
        files = env.list_files("**/*")
        if search_path != ".":
            files = [f for f in files if f == search_path or f.startswith(search_path + "/")]
        if glob_filter:
            files = [f for f in files if fnmatch.fnmatch(f.split("/")[-1], glob_filter)]

        matches: list[str] = []
        for filepath in sorted(files):
            try:
                content = env.read_file(filepath)
            except (FileNotFoundError, UnicodeDecodeError):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{filepath}:{i}: {line}")

        if not matches:
            return ToolResult(output="No matches found.")
        return ToolResult(output="\n".join(matches))

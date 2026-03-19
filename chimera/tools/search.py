# chimera/tools/search.py
from __future__ import annotations

import fnmatch
import re
from typing import TYPE_CHECKING, Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

if TYPE_CHECKING:
    from chimera.core.operations import SearchOps


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

    def __init__(self, ops: SearchOps | None = None) -> None:
        self._ops = ops

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        pattern = args["pattern"]
        search_path = args.get("path", ".")
        glob_filter = args.get("glob")

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(output="", error=f"Invalid regex: {e}")

        if self._ops is not None:
            raw = self._ops.search_files(pattern, search_path)
            # raw entries are already "path:lineno: line" strings from LocalSearchOps
            if glob_filter:
                filtered = []
                for entry in raw:
                    # entry format: "filepath:lineno: line"
                    file_part = entry.split(":")[0]
                    if fnmatch.fnmatch(file_part.split("/")[-1], glob_filter):
                        filtered.append(entry)
                raw = filtered
            if not raw:
                return ToolResult(output="No matches found.")
            return ToolResult(output="\n".join(raw))

        assert env is not None

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

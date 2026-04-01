"""Tool search tool for discovering available tools at runtime.

Allows the model to search for tools by keyword when the full tool
list has been deferred.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from chimera.core.tool import BaseTool
from chimera.types import ToolResult


class ToolSearchTool(BaseTool):
    """Search available tools by keyword.

    Matches against tool names and descriptions (case-insensitive).
    Returns a JSON list of matching tool schemas.
    """

    name = "tool_search"
    description = "Search for available tools by keyword"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query to find tools by name or description",
            },
        },
        "required": ["query"],
    }

    def __init__(self, all_tools: list[BaseTool]) -> None:
        self._all_tools = all_tools

    def execute(self, args: dict[str, Any], env: Any) -> ToolResult:
        """Search tools by name and description."""
        query = args.get("query", "").lower()
        matches = []
        for t in self._all_tools:
            if query in t.name.lower() or query in t.description.lower():
                matches.append({
                    "name": t.name,
                    "description": t.description,
                })
        return ToolResult(output=json.dumps(matches, indent=2))

    async def async_execute(self, args: dict[str, Any], env: Any) -> ToolResult:
        """Async version of execute."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.execute, args, env)

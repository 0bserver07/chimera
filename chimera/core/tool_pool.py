"""Tool pool with deferred loading support.

Provides :class:`DeferredToolConfig` and :class:`ToolPool` for managing
eager vs. deferred tool exposure to the model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from chimera.core.tool import BaseTool
from chimera.types import ToolResult

# Default tools that should always be eagerly available
_DEFAULT_ALWAYS_EAGER: set[str] = {
    "read", "write", "edit", "bash", "search", "list_files",
    "git", "test", "tool_search",
}


@dataclass
class DeferredToolConfig:
    """Configuration for deferred tool loading."""

    max_eager_tools: int = 30
    always_eager: set[str] = field(default_factory=lambda: set(_DEFAULT_ALWAYS_EAGER))


class _InternalToolSearchTool(BaseTool):
    """Lightweight tool-search placeholder used by ToolPool.

    The full :class:`~chimera.tools.tool_search.ToolSearchTool` provides
    richer functionality; this is used only when the real one is not
    already present in the tool list.
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
        query = args.get("query", "").lower()
        matches = [
            {"name": t.name, "description": t.description}
            for t in self._all_tools
            if query in t.name.lower() or query in t.description.lower()
        ]
        return ToolResult(output=str(matches))


class ToolPool:
    """Manages eager and deferred tool sets.

    When the total tool count exceeds *max_eager_tools*, only the
    *always_eager* tools (plus a ``tool_search`` helper) are exposed
    initially.  The model can use ``tool_search`` to discover and
    activate additional tools.
    """

    def __init__(
        self,
        all_tools: list[BaseTool],
        config: DeferredToolConfig | None = None,
    ) -> None:
        self._all_tools = list(all_tools)
        self._config = config or DeferredToolConfig()

    def get_eager_tools(self) -> list[BaseTool]:
        """Return tools to expose immediately.

        If under the limit, returns all tools.  Otherwise returns only
        the *always_eager* subset plus a ``tool_search`` tool.
        """
        if len(self._all_tools) <= self._config.max_eager_tools:
            return list(self._all_tools)

        eager: list[BaseTool] = []
        has_tool_search = False
        for t in self._all_tools:
            if t.name in self._config.always_eager:
                eager.append(t)
                if t.name == "tool_search":
                    has_tool_search = True

        if not has_tool_search:
            eager.append(_InternalToolSearchTool(self._all_tools))

        return eager

    def get_all_tools(self) -> list[BaseTool]:
        """Return every registered tool."""
        return list(self._all_tools)

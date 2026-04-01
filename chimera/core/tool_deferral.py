"""Tool deferral management — eager vs. deferred tool loading.

When the total number of registered tools exceeds :attr:`MAX_EAGER`, only
the tools in :attr:`ALWAYS_EAGER` are included in the system prompt.  The
rest become available through a ``ToolSearch`` meta-tool, reducing prompt
size and cost.
"""

from __future__ import annotations

from chimera.core.tool import BaseTool


class ToolDeferralManager:
    """Manage eager vs deferred tool loading.

    Args:
        all_tools: The complete list of tools available to the agent.
    """

    ALWAYS_EAGER: set[str] = {
        "bash",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "agent",
        "skill",
        "task_list",
        "task_output",
        "task_stop",
    }
    MAX_EAGER: int = 30

    def __init__(self, all_tools: list[BaseTool]) -> None:
        self._all: dict[str, BaseTool] = {t.name: t for t in all_tools}

    def get_eager_tools(self) -> list[BaseTool]:
        """Tools included in the system prompt."""
        if len(self._all) <= self.MAX_EAGER:
            return list(self._all.values())
        return [t for t in self._all.values() if t.name in self.ALWAYS_EAGER]

    def get_deferred_tools(self) -> list[BaseTool]:
        """Tools only available via ToolSearch."""
        if len(self._all) <= self.MAX_EAGER:
            return []
        return [t for t in self._all.values() if t.name not in self.ALWAYS_EAGER]

    def search(self, query: str) -> list[BaseTool]:
        """Search all tools by name/description.

        Args:
            query: Case-insensitive keyword to match against tool names and
                descriptions.

        Returns:
            List of matching tools.
        """
        query_lower = query.lower()
        return [
            t
            for t in self._all.values()
            if query_lower in t.name.lower() or query_lower in (t.description or "").lower()
        ]

    def get_tool(self, name: str) -> BaseTool | None:
        """Get any tool by name (eager or deferred)."""
        return self._all.get(name)

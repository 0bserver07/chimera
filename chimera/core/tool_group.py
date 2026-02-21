# chimera/core/tool_group.py
from __future__ import annotations

from chimera.core.tool import BaseTool


class ToolGroup:
    """A named collection of tools. Like a preset toolkit."""

    def __init__(self, name: str, tools: list[BaseTool]) -> None:
        self.name = name
        self.tools = list(tools)
        self._map = {t.name: t for t in self.tools}

    def has(self, name: str) -> bool:
        return name in self._map

    def get(self, name: str) -> BaseTool | None:
        return self._map.get(name)

    def add(self, tool: BaseTool) -> None:
        self.tools.append(tool)
        self._map[tool.name] = tool

    def __iter__(self):
        return iter(self.tools)

    def __len__(self):
        return len(self.tools)


# Predefined groups
def _make_default_tools() -> ToolGroup:
    from chimera.tools.read import ReadFileTool
    from chimera.tools.write import WriteFileTool
    from chimera.tools.bash import BashTool
    return ToolGroup("default", [ReadFileTool(), WriteFileTool(), BashTool()])


DEFAULT_TOOLS = _make_default_tools()

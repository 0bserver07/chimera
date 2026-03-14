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
    from chimera.tools.image_read import ImageReadTool
    return ToolGroup("default", [ReadFileTool(), WriteFileTool(), BashTool(), ImageReadTool()])


DEFAULT_TOOLS = _make_default_tools()


def _make_agent_tools() -> ToolGroup:
    """Extended tool set for interactive agent sessions."""
    from chimera.tools.read import ReadFileTool
    from chimera.tools.write import WriteFileTool
    from chimera.tools.bash import BashTool
    from chimera.tools.image_read import ImageReadTool
    from chimera.tools.edit import EditFileTool
    from chimera.tools.search import SearchTool
    from chimera.tools.list_files import ListFilesTool
    from chimera.tools.test import TestTool
    from chimera.tools.git import GitTool
    from chimera.tools.replace_in_file import ReplaceInFileTool
    from chimera.tools.repo_map import RepoMapTool
    from chimera.tools.think import ThinkTool
    from chimera.tools.todo import TodoTool
    return ToolGroup("agent", [
        ReadFileTool(), WriteFileTool(), EditFileTool(),
        BashTool(), SearchTool(), ListFilesTool(),
        TestTool(), GitTool(), ReplaceInFileTool(),
        ImageReadTool(), RepoMapTool(),
        ThinkTool(), TodoTool(),
    ])


AGENT_TOOLS = _make_agent_tools()

# chimera/core/tool_group.py
from __future__ import annotations

import functools
from typing import Any, Iterator

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

    def __iter__(self) -> Iterator[BaseTool]:
        return iter(self.tools)

    def __len__(self) -> int:
        return len(self.tools)


# WHY (W2 circular-import bug): the previous module-level
# ``DEFAULT_TOOLS = _make_default_tools()`` and
# ``AGENT_TOOLS = _make_agent_tools()`` calls ran at import time.  Any module
# that imported ``chimera.core`` (or transitively pulled it in via
# ``chimera.tools.__init__`` re-exports) would trigger a fresh import of
# ``chimera.tools.read``, ``...write``, etc.  When the very first symbol
# requested was inside ``chimera.tools`` itself (e.g. ``from
# chimera.tools.task_tool import TaskTool``) the loader entered
# ``chimera.tools.__init__`` -> ``chimera.tools.read`` -> ``chimera.core``
# -> ``chimera.core.tool_group`` -> ``_make_default_tools`` ->
# ``chimera.tools.read`` again, which was still partially initialised, raising
# ``ImportError: cannot import name 'ReadFileTool'``.  Wrapping the factories
# in ``functools.cache`` and exposing the constants through ``__getattr__``
# defers concrete instantiation until the first attribute access, by which
# point both submodule trees are fully populated.


@functools.cache
def _make_default_tools() -> ToolGroup:
    from chimera.tools.read import ReadFileTool
    from chimera.tools.write import WriteFileTool
    from chimera.tools.bash import BashTool
    from chimera.tools.image_read import ImageReadTool
    return ToolGroup("default", [ReadFileTool(), WriteFileTool(), BashTool(), ImageReadTool()])


@functools.cache
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
    from chimera.tools.verify import VerifyTool
    from chimera.tools.web_search import WebSearchTool
    from chimera.tools.apply_patch import ApplyPatchTool
    from chimera.tools.write_guard import WriteGuardTool
    from chimera.tools.notebook_edit import NotebookEditTool
    from chimera.tools.worktree_tool import EnterWorktreeTool, ExitWorktreeTool
    from chimera.tools.cron_tools import (
        CronCreateTool, CronDeleteTool, CronListTool,
    )
    return ToolGroup("agent", [
        ReadFileTool(), WriteFileTool(), EditFileTool(),
        BashTool(), SearchTool(), ListFilesTool(),
        TestTool(), GitTool(), ReplaceInFileTool(),
        ImageReadTool(), RepoMapTool(),
        ThinkTool(),
        # WHY: persist=True for CC-parity /resume; tests construct bare
        # TodoTool() which keeps the default persist=False.
        TodoTool(persist=True),
        VerifyTool(), WebSearchTool(),
        # WHY (W13-G1): apply_patch is a shared default for the ferret +
        # otter coding-agent CLIs so multi-file structured edits are
        # available without needing to pin a custom tool list.
        ApplyPatchTool(),
        # WHY (W13-G13): write_guard surfaces the write_file vs edit_file
        # invariant; notebook_edit / worktree / cron close the
        # AGENT_TOOLS gap that previously left mink (and friends) without
        # a default Jupyter / git-worktree / scheduled-task surface.
        WriteGuardTool(),
        NotebookEditTool(),
        EnterWorktreeTool(), ExitWorktreeTool(),
        CronCreateTool(), CronListTool(), CronDeleteTool(),
    ])


# Public lazy attributes.  ``DEFAULT_TOOLS`` / ``AGENT_TOOLS`` keep their
# constant-import look-and-feel for callers; the underlying ``ToolGroup`` is
# built on first access (via ``__getattr__`` below) and cached for the
# process lifetime.  ``noqa: F822`` because ruff can't see module-level
# ``__getattr__``-provided names.
__all__ = [  # noqa: F822
    "ToolGroup",
    "DEFAULT_TOOLS",
    "AGENT_TOOLS",
    "create_default_tools",
]


def __getattr__(name: str) -> Any:
    if name == "DEFAULT_TOOLS":
        return _make_default_tools()
    if name == "AGENT_TOOLS":
        return _make_agent_tools()
    raise AttributeError(f"module 'chimera.core.tool_group' has no attribute {name!r}")


def create_default_tools(
    read_ops: Any = None,
    write_ops: Any = None,
    bash_ops: Any = None,
    search_ops: Any = None,
) -> ToolGroup:
    """Create default tool set with optional operation backends.

    Args:
        read_ops: Optional ReadOps backend for file reading.
        write_ops: Optional WriteOps backend for file writing.
        bash_ops: Optional BashOps backend for command execution.
        search_ops: Optional SearchOps backend for file listing/search.

    Returns:
        ToolGroup configured with the given backends.
    """
    from chimera.tools.read import ReadFileTool
    from chimera.tools.write import WriteFileTool
    from chimera.tools.bash import BashTool
    from chimera.tools.image_read import ImageReadTool
    return ToolGroup("default", [
        ReadFileTool(ops=read_ops),
        WriteFileTool(read_ops=read_ops, write_ops=write_ops),
        BashTool(ops=bash_ops),
        ImageReadTool(),
    ])

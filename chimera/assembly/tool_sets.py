"""Named tool collections for different agent presets.

Each function returns a fresh list of tool instances so that independent
agents never share mutable state.
"""
from __future__ import annotations

from typing import Any

from chimera.core.tool import BaseTool


def coding_tools(**kwargs: Any) -> list[BaseTool]:
    """Full coding tool set."""
    from chimera.tools.bash import BashTool
    from chimera.tools.cached_read import CachedReadTool
    from chimera.tools.write import WriteFileTool
    from chimera.tools.edit import EditFileTool
    from chimera.tools.search import SearchTool
    from chimera.tools.list_files import ListFilesTool
    from chimera.tools.replace_in_file import ReplaceInFileTool
    from chimera.tools.git import GitTool
    from chimera.tools.test import TestTool
    from chimera.tools.think import ThinkTool
    from chimera.tools.todo import TodoTool
    from chimera.tools.web_fetch import WebFetchTool
    from chimera.tools.web_search import WebSearchTool
    from chimera.tools.ask_user import AskUserTool
    from chimera.tools.agent_tool import AgentTool
    from chimera.tools.skill_tool import SkillTool
    from chimera.tools.tool_search import ToolSearchTool
    from chimera.tools.task_tools import TaskOutputTool, TaskStopTool, TaskListTool
    from chimera.tools.apply_patch import ApplyPatchTool
    from chimera.tools.batch import BatchTool
    from chimera.tools.plan_mode import EnterPlanModeTool, ExitPlanModeTool

    file_cache = kwargs.get("file_cache")
    spawner = kwargs.get("spawner")
    task_manager = kwargs.get("task_manager")
    command_registry = kwargs.get("command_registry")

    # Build a default CommandRegistry when none is supplied so SkillTool
    # always receives a registry instance.
    if command_registry is None:
        from chimera.commands.registry import CommandRegistry
        command_registry = CommandRegistry()

    tools = [
        BashTool(),
        CachedReadTool(cache=file_cache),
        WriteFileTool(),
        EditFileTool(),
        SearchTool(),
        ListFilesTool(),
        ReplaceInFileTool(),
        GitTool(),
        TestTool(),
        ThinkTool(),
        # WHY: persist=True so /resume rehydrates todos in production
        # CLI use; bare TodoTool() in tests stays ephemeral.
        TodoTool(persist=True),
        WebFetchTool(),
        WebSearchTool(),
        AskUserTool(),
        AgentTool(spawner=spawner),
        SkillTool(registry=command_registry, spawner=spawner),
        ToolSearchTool([]),  # Populated after all tools are known
        TaskOutputTool(task_manager=task_manager),
        TaskStopTool(task_manager=task_manager),
        TaskListTool(task_manager=task_manager),
        ApplyPatchTool(),
    ]
    # Plan mode tools — share state so exit can deactivate enter's flag
    enter_plan = EnterPlanModeTool()
    exit_plan = ExitPlanModeTool(enter_tool=enter_plan)
    tools.extend([enter_plan, exit_plan])
    # BatchTool needs the full tool_map so it can dispatch to other tools
    tool_map = {t.name: t for t in tools}
    tools.append(BatchTool(tool_map=tool_map))
    return tools


def minimal_tools(**kwargs: Any) -> list[BaseTool]:
    """Minimal tool set for simple tasks."""
    from chimera.tools.bash import BashTool
    from chimera.tools.read import ReadFileTool
    from chimera.tools.write import WriteFileTool
    from chimera.tools.edit import EditFileTool

    return [BashTool(), ReadFileTool(), WriteFileTool(), EditFileTool()]


def explore_tools(**kwargs: Any) -> list[BaseTool]:
    """Read-only tools for codebase exploration."""
    from chimera.tools.cached_read import CachedReadTool
    from chimera.tools.search import SearchTool
    from chimera.tools.list_files import ListFilesTool

    return [
        CachedReadTool(cache=kwargs.get("file_cache")),
        SearchTool(),
        ListFilesTool(),
    ]

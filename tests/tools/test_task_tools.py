"""Tests for chimera.tools.task_tools — IG-5."""
from __future__ import annotations

from chimera.core.task_manager import TaskManager
from chimera.tools.task_tools import TaskListTool, TaskOutputTool, TaskStopTool


class TestTaskOutputTool:
    """TaskOutputTool reads output from background tasks."""

    def test_no_task_manager(self):
        tool = TaskOutputTool()
        result = tool.execute({"task_id": "abc"}, env=None)
        assert result.error is not None

    def test_task_not_found(self):
        tm = TaskManager()
        tool = TaskOutputTool(task_manager=tm)
        result = tool.execute({"task_id": "nonexistent"}, env=None)
        assert result.error is not None
        assert "not found" in result.error.lower()

    def test_task_found_returns_status(self):
        tm = TaskManager()
        task = tm.register(agent_id="a1", description="test task")
        tool = TaskOutputTool(task_manager=tm)
        result = tool.execute({"task_id": task.task_id}, env=None)
        assert result.error is None
        assert "running" in result.output.lower()


class TestTaskStopTool:
    """TaskStopTool stops background tasks."""

    def test_no_task_manager(self):
        tool = TaskStopTool()
        result = tool.execute({"task_id": "abc"}, env=None)
        assert result.error is not None

    def test_stop_task(self):
        tm = TaskManager()
        task = tm.register(agent_id="a1", description="test task")
        tool = TaskStopTool(task_manager=tm)
        result = tool.execute({"task_id": task.task_id}, env=None)
        assert result.error is None
        assert "stopped" in result.output.lower()
        assert tm.get(task.task_id).status == "stopped"


class TestTaskListTool:
    """TaskListTool lists background tasks."""

    def test_no_task_manager(self):
        tool = TaskListTool()
        result = tool.execute({}, env=None)
        assert "no tasks" in result.output.lower()

    def test_empty_list(self):
        tm = TaskManager()
        tool = TaskListTool(task_manager=tm)
        result = tool.execute({}, env=None)
        assert "no active tasks" in result.output.lower()

    def test_lists_tasks(self):
        tm = TaskManager()
        task = tm.register(agent_id="a1", description="my task")
        tool = TaskListTool(task_manager=tm)
        result = tool.execute({}, env=None)
        assert task.task_id in result.output
        assert "my task" in result.output
        assert "running" in result.output.lower()

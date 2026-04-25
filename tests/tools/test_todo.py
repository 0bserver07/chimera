# tests/test_todo.py
"""Tests for the TodoTool."""
import pytest

from chimera.tools.todo import TodoItem, TodoTool


@pytest.fixture
def tool():
    return TodoTool()


# ------------------------------------------------------------------
# Schema / identity
# ------------------------------------------------------------------


def test_name_and_schema(tool):
    assert tool.name == "todo"
    assert "action" in tool.parameters["properties"]
    assert "task" in tool.parameters["properties"]
    assert "action" in tool.parameters["required"]


# ------------------------------------------------------------------
# add
# ------------------------------------------------------------------


def test_add_task(tool):
    result = tool.execute({"action": "add", "task": "Write unit tests"})
    assert result.error is None
    assert "#1" in result.output
    assert "Write unit tests" in result.output


def test_add_multiple(tool):
    r1 = tool.execute({"action": "add", "task": "Task A"})
    r2 = tool.execute({"action": "add", "task": "Task B"})
    r3 = tool.execute({"action": "add", "task": "Task C"})
    assert "#1" in r1.output
    assert "#2" in r2.output
    assert "#3" in r3.output


def test_add_no_description(tool):
    result = tool.execute({"action": "add", "task": ""})
    assert result.error is not None
    assert "required" in result.error.lower()


# ------------------------------------------------------------------
# complete
# ------------------------------------------------------------------


def test_complete_task(tool):
    tool.execute({"action": "add", "task": "Do the thing"})
    result = tool.execute({"action": "complete", "task": "1"})
    assert result.error is None
    assert "#1" in result.output
    assert "Completed" in result.output
    assert tool.items[0].done is True


def test_complete_nonexistent(tool):
    result = tool.execute({"action": "complete", "task": "999"})
    assert result.error is not None
    assert "999" in result.error


def test_complete_invalid_id(tool):
    result = tool.execute({"action": "complete", "task": "not-a-number"})
    assert result.error is not None
    assert "Invalid" in result.error


# ------------------------------------------------------------------
# list
# ------------------------------------------------------------------


def test_list_empty(tool):
    result = tool.execute({"action": "list"})
    assert result.error is None
    assert "No tasks" in result.output


def test_list_with_tasks(tool):
    tool.execute({"action": "add", "task": "Alpha"})
    tool.execute({"action": "add", "task": "Beta"})
    tool.execute({"action": "add", "task": "Gamma"})
    tool.execute({"action": "complete", "task": "2"})

    result = tool.execute({"action": "list"})
    assert result.error is None
    lines = result.output.strip().split("\n")
    assert len(lines) == 3
    assert "[todo]" in lines[0]
    assert "[done]" in lines[1]
    assert "[todo]" in lines[2]


# ------------------------------------------------------------------
# unknown action
# ------------------------------------------------------------------


def test_unknown_action(tool):
    result = tool.execute({"action": "delete"})
    assert result.error is not None
    assert "Unknown action" in result.error


# ------------------------------------------------------------------
# items property
# ------------------------------------------------------------------


def test_items_property(tool):
    tool.execute({"action": "add", "task": "First"})
    items = tool.items
    assert len(items) == 1
    assert items[0].task == "First"
    # Verify it's a copy, not the internal list
    items.append(TodoItem(id=99, task="Ghost"))
    assert len(tool.items) == 1

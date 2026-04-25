# tests/test_think.py
import pytest

from chimera.tools.think import ThinkTool
from chimera.types import ToolResult


@pytest.fixture
def tool():
    return ThinkTool()


def test_name_and_schema(tool):
    assert tool.name == "think"
    assert "thought" in tool.parameters["properties"]
    assert "thought" in tool.parameters["required"]


def test_execute_records_thought(tool):
    result = tool.execute({"thought": "I should check the logs first."}, env=None)
    assert isinstance(result, ToolResult)
    assert result.output == "Thought recorded."
    assert result.metadata["thought"] == "I should check the logs first."
    assert result.error is None


def test_no_env_required(tool):
    result = tool.execute({"thought": "env is None and that is fine"}, env=None)
    assert result.success
    assert result.metadata["thought"] == "env is None and that is fine"

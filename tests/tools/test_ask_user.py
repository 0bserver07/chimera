# tests/test_ask_user.py
import pytest

from chimera.tools.ask_user import AskUserTool
from chimera.types import ToolResult


@pytest.fixture
def tool():
    return AskUserTool()


def test_name_and_schema(tool):
    assert tool.name == "ask_user"
    assert "question" in tool.parameters["properties"]
    assert "choices" in tool.parameters["properties"]
    assert "question" in tool.parameters["required"]


def test_callback_called():
    calls = []

    def cb(question, choices):
        calls.append((question, choices))
        return "yes"

    t = AskUserTool(callback=cb)
    result = t.execute({"question": "Continue?"}, env=None)
    assert len(calls) == 1
    assert calls[0] == ("Continue?", None)
    assert isinstance(result, ToolResult)


def test_callback_with_choices():
    calls = []

    def cb(question, choices):
        calls.append((question, choices))
        return "option_a"

    t = AskUserTool(callback=cb)
    t.execute({"question": "Pick one", "choices": ["a", "b", "c"]}, env=None)
    assert calls[0][1] == ["a", "b", "c"]


def test_callback_without_choices():
    calls = []

    def cb(question, choices):
        calls.append((question, choices))
        return "sure"

    t = AskUserTool(callback=cb)
    t.execute({"question": "Are you sure?"}, env=None)
    assert calls[0][1] is None


def test_default_no_callback():
    t = AskUserTool()
    assert t._callback is None
    # Construction without a callback should work fine
    assert t.name == "ask_user"


def test_callback_response_in_output():
    def cb(question, choices):
        return "42"

    t = AskUserTool(callback=cb)
    result = t.execute({"question": "What is the answer?"}, env=None)
    assert result.output == "42"
    assert result.error is None

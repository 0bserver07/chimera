# tests/test_tools_verify.py
from unittest.mock import MagicMock

import pytest

from chimera.tools.verify import VerifyTool
from chimera.types import CommandResult


@pytest.fixture
def tool():
    return VerifyTool()


@pytest.fixture
def env():
    e = MagicMock()
    return e


def test_tool_name(tool):
    assert tool.name == "verify_answer"


def test_tool_has_parameters(tool):
    assert "code" in tool.parameters["properties"]
    assert "code" in tool.parameters["required"]


def test_verify_passing(tool, env):
    env.run_command.return_value = CommandResult(stdout="True\n", stderr="", exit_code=0)

    result = tool.execute({"code": "print(2 + 2 == 4)"}, env)
    assert result.error is None
    assert "True" in result.output
    assert result.metadata.get("verified") is True


def test_verify_failing(tool, env):
    env.run_command.return_value = CommandResult(stdout="False\n", stderr="", exit_code=0)

    result = tool.execute({"code": "print(2 + 2 == 5)"}, env)
    assert result.error is None
    assert "False" in result.output
    assert result.metadata.get("verified") is False


def test_verify_code_error(tool, env):
    env.run_command.return_value = CommandResult(
        stdout="", stderr="NameError: name 'x' is not defined", exit_code=1
    )

    result = tool.execute({"code": "print(x)"}, env)
    assert result.error is not None
    assert "NameError" in result.output


def test_verify_timeout(tool, env):
    env.run_command.return_value = CommandResult(stdout="True\n", stderr="", exit_code=0)
    tool.execute({"code": "import time; time.sleep(999)", "timeout": 5}, env)
    env.run_command.assert_called_once()
    # Verify timeout was passed to run_command
    call_args = env.run_command.call_args
    assert call_args[1].get("timeout") == 5 or (len(call_args[0]) > 1 and call_args[0][1] == 5)


def test_to_openai_schema(tool):
    schema = tool.to_openai_schema()
    assert schema["function"]["name"] == "verify_answer"


def test_to_anthropic_schema(tool):
    schema = tool.to_anthropic_schema()
    assert schema["name"] == "verify_answer"

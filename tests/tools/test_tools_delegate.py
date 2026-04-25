# tests/test_tools_delegate.py
from __future__ import annotations

import tempfile

import pytest

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.tools.delegate import DelegateTool


class EchoProvider(Provider):
    """Returns the task text as output, no tool calls."""
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        # Find the last user message
        last_user = ""
        for m in messages:
            if m.role == "user":
                last_user = m.content
        return Response(content=f"Processed: {last_user}", tool_calls=[], usage={"input_tokens": 10, "output_tokens": 10})

    @property
    def context_window(self): return 100_000
    @property
    def supports_tool_use(self): return True
    @property
    def model_name(self): return "echo"


class TestDelegateTool:
    def test_delegate_runs_sub_agent(self):
        sub_agent = Agent(provider=EchoProvider(), tools=[], loop=ReAct(max_steps=5))
        tool = DelegateTool(sub_agent=sub_agent)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            result = tool.execute({"task": "Fix the bug in main.py"}, env)
            assert result.success
            assert "Processed:" in result.output
            assert "Fix the bug" in result.output

    def test_delegate_schema(self):
        sub_agent = Agent(provider=EchoProvider())
        tool = DelegateTool(sub_agent=sub_agent)
        assert tool.name == "delegate"
        schema = tool.to_anthropic_schema()
        assert "task" in str(schema)

    def test_delegate_custom_name(self):
        sub_agent = Agent(provider=EchoProvider())
        tool = DelegateTool(sub_agent=sub_agent, tool_name="ask_researcher")
        assert tool.name == "ask_researcher"

    def test_delegate_rejects_none_sub_agent(self):
        with pytest.raises(ValueError, match="sub_agent"):
            DelegateTool(sub_agent=None)

    def test_delegate_rejects_empty_task(self):
        sub_agent = Agent(provider=EchoProvider())
        tool = DelegateTool(sub_agent=sub_agent)
        result = tool.execute({"task": ""}, None)
        assert result.error is not None
        assert "task" in result.error.lower()

    def test_delegate_rejects_missing_task(self):
        sub_agent = Agent(provider=EchoProvider())
        tool = DelegateTool(sub_agent=sub_agent)
        result = tool.execute({}, None)
        assert result.error is not None

    def test_delegate_class_has_default_name(self):
        """Class-level introspection must find a ``name`` attribute."""
        assert DelegateTool.name == "delegate"

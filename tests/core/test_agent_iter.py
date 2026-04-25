"""Tests for Agent.iter_steps() and Session.iter_chat()."""
from __future__ import annotations

from typing import Any


from chimera.core.agent import Agent
from chimera.core.loop import drain_steps
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider, Response
from chimera.sessions.session import Session
from chimera.types import AgentResult, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockProvider(Provider):
    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None) -> Response:
        resp = self._responses[self._idx]
        self._idx += 1
        return resp

    @property
    def context_window(self) -> int:
        return 4096

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock"


class EchoTool(BaseTool):
    name = "echo"
    description = "Echoes input"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        return ToolResult(output=f"echo: {args}")


def text_response(content: str) -> Response:
    return Response(content=content, tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5})


def tool_response(content: str, call_id: str = "c1") -> Response:
    return Response(
        content=content,
        tool_calls=[ToolCall(id=call_id, name="echo", arguments={"msg": "hi"})],
        usage={"input_tokens": 10, "output_tokens": 5},
    )


# ---------------------------------------------------------------------------
# Tests: Agent.iter_steps
# ---------------------------------------------------------------------------

class TestAgentIterSteps:
    def test_text_only(self) -> None:
        provider = MockProvider([text_response("Hello")])
        agent = Agent(provider=provider, tools=[])
        steps = list(agent.iter_steps("say hi", None))
        assert len(steps) == 1
        assert steps[0].done is True

    def test_tool_then_text(self) -> None:
        provider = MockProvider([tool_response("echo"), text_response("Done")])
        agent = Agent(provider=provider, tools=[EchoTool()])
        steps = list(agent.iter_steps("echo", None))
        assert len(steps) == 2
        assert steps[0].done is False
        assert steps[1].done is True

    def test_returns_agent_result(self) -> None:
        provider = MockProvider([text_response("Hello")])
        agent = Agent(provider=provider, tools=[])
        gen = agent.iter_steps("hi", None)
        result = drain_steps(gen)
        assert isinstance(result, AgentResult)
        assert result.success is True
        assert result.output == "Hello"


# ---------------------------------------------------------------------------
# Tests: Session.iter_chat
# ---------------------------------------------------------------------------

class TestSessionIterChat:
    def test_iter_chat_basic(self) -> None:
        provider = MockProvider([text_response("Hi there")])
        agent = Agent(provider=provider, tools=[])
        session = Session(agent=agent)
        steps = list(session.iter_chat("hello"))
        assert len(steps) == 1
        assert steps[0].done is True

    def test_iter_chat_with_tools(self) -> None:
        provider = MockProvider([tool_response("echo"), text_response("Done")])
        agent = Agent(provider=provider, tools=[EchoTool()])
        session = Session(agent=agent)
        steps = list(session.iter_chat("echo something"))
        assert len(steps) == 2
        assert steps[0].done is False
        assert steps[1].done is True

    def test_iter_chat_returns_agent_result(self) -> None:
        provider = MockProvider([text_response("Answer")])
        agent = Agent(provider=provider, tools=[])
        session = Session(agent=agent)
        gen = session.iter_chat("question")
        result = drain_steps(gen)
        assert isinstance(result, AgentResult)
        assert result.success is True

    def test_iter_chat_multi_turn(self) -> None:
        """Messages accumulate across iter_chat calls."""
        provider = MockProvider([
            text_response("First"),
            text_response("Second"),
        ])
        agent = Agent(provider=provider, tools=[])
        session = Session(agent=agent)

        list(session.iter_chat("msg1"))
        list(session.iter_chat("msg2"))

        # Session should have accumulated messages
        assert len(session.messages) >= 4  # user1, assistant1, user2, assistant2

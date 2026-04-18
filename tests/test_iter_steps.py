"""Tests for ReAct.iter_steps() and drain_steps()."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


from chimera.core.context import Context
from chimera.core.loop import ReAct, drain_steps
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import AgentResult, Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockProvider(Provider):
    """Provider that returns pre-configured responses in sequence."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
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


def tool_response(content: str, tool_name: str = "echo", call_id: str = "c1") -> Response:
    return Response(
        content=content,
        tool_calls=[ToolCall(id=call_id, name=tool_name, arguments={"msg": "hi"})],
        usage={"input_tokens": 10, "output_tokens": 5},
    )


# ---------------------------------------------------------------------------
# Tests: iter_steps
# ---------------------------------------------------------------------------

class TestIterStepsBasic:
    def test_text_only_one_step(self) -> None:
        provider = MockProvider([text_response("Hello")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("say hi"))

        steps = list(loop.iter_steps(provider, [], context, None))
        assert len(steps) == 1
        assert steps[0].done is True
        assert steps[0].step == 1

    def test_tool_then_text(self) -> None:
        provider = MockProvider([tool_response("I'll echo"), text_response("Done")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("echo something"))

        steps = list(loop.iter_steps(provider, [EchoTool()], context, None))
        assert len(steps) == 2
        assert steps[0].done is False
        assert len(steps[0].tool_calls) == 1
        assert steps[1].done is True

    def test_max_steps_reached(self) -> None:
        # Always returns tool calls — will hit max_steps
        responses = [tool_response("step", call_id=f"c{i}") for i in range(5)]
        provider = MockProvider(responses)
        loop = ReAct(max_steps=3)
        context = Context(system="test")
        context.add(Message.user("go"))

        steps = list(loop.iter_steps(provider, [EchoTool()], context, None))
        # 3 tool steps + 1 final "max steps reached" step
        assert len(steps) == 4
        assert steps[-1].done is True

    def test_step_cost(self) -> None:
        provider = MockProvider([text_response("ok")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))

        steps = list(loop.iter_steps(provider, [], context, None))
        assert steps[0].cost >= 0.0


class TestIterStepsAgentResult:
    def test_returns_agent_result(self) -> None:
        provider = MockProvider([text_response("Hello")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))

        gen = loop.iter_steps(provider, [], context, None)
        result = None
        try:
            while True:
                next(gen)
        except StopIteration as e:
            result = e.value

        assert isinstance(result, AgentResult)
        assert result.success is True
        assert result.output == "Hello"


class TestIterStepsPermissions:
    def test_ask_yields_pending_approval(self) -> None:
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.base import PermissionAction

        policy = MagicMock()
        policy.evaluate.return_value = PermissionAction.ASK
        config = LoopConfig(permissions=policy)

        provider = MockProvider([
            tool_response("I'll echo"),
            text_response("Done"),
        ])
        loop = ReAct(max_steps=10, config=config)
        context = Context(system="test")
        context.add(Message.user("echo"))

        gen = loop.iter_steps(provider, [EchoTool()], context, None)
        step = next(gen)

        assert step.pending_approval is not None
        assert step.pending_approval.tool_name == "echo"
        assert not step.pending_approval.decided

        # Approve and continue
        step.pending_approval.approve()
        remaining_steps = list(gen)
        assert len(remaining_steps) >= 1

    def test_deny_continues_loop(self) -> None:
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.base import PermissionAction

        policy = MagicMock()
        # First call: ASK, subsequent: ALLOW
        policy.evaluate.side_effect = [PermissionAction.ASK] + [PermissionAction.ALLOW] * 10
        config = LoopConfig(permissions=policy)

        provider = MockProvider([
            tool_response("I'll echo"),
            text_response("Done after deny"),
        ])
        loop = ReAct(max_steps=10, config=config)
        context = Context(system="test")
        context.add(Message.user("echo"))

        gen = loop.iter_steps(provider, [EchoTool()], context, None)
        step = next(gen)

        assert step.pending_approval is not None
        step.pending_approval.deny("Nope")

        remaining = list(gen)
        assert any(s.done for s in remaining)


# ---------------------------------------------------------------------------
# Tests: drain_steps
# ---------------------------------------------------------------------------

class TestDrainSteps:
    def test_auto_denies_pending(self) -> None:
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.base import PermissionAction

        policy = MagicMock()
        policy.evaluate.side_effect = [PermissionAction.ASK] + [PermissionAction.ALLOW] * 10
        config = LoopConfig(permissions=policy)

        provider = MockProvider([
            tool_response("I'll echo"),
            text_response("Done"),
        ])
        loop = ReAct(max_steps=10, config=config)
        context = Context(system="test")
        context.add(Message.user("echo"))

        result = drain_steps(loop.iter_steps(provider, [EchoTool()], context, None))
        assert isinstance(result, AgentResult)

    def test_basic_completion(self) -> None:
        provider = MockProvider([text_response("Hello")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))

        result = drain_steps(loop.iter_steps(provider, [], context, None))
        assert result.success is True
        assert result.output == "Hello"


# ---------------------------------------------------------------------------
# Tests: run() regression
# ---------------------------------------------------------------------------

class TestRunRegression:
    def test_text_only(self) -> None:
        provider = MockProvider([text_response("Hello")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))
        result = loop.run(provider, [], context, None)
        assert result.success is True
        assert result.output == "Hello"

    def test_tool_then_text(self) -> None:
        provider = MockProvider([tool_response("I'll echo"), text_response("Done")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("echo"))
        result = loop.run(provider, [EchoTool()], context, None)
        assert result.success is True
        assert result.steps == 2
        assert result.tool_calls_total == 1

    def test_max_steps(self) -> None:
        responses = [tool_response("step", call_id=f"c{i}") for i in range(5)]
        provider = MockProvider(responses)
        loop = ReAct(max_steps=3)
        context = Context(system="test")
        context.add(Message.user("go"))
        result = loop.run(provider, [EchoTool()], context, None)
        assert result.success is False
        assert result.error == "Max steps reached"

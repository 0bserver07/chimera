"""Tests for iter_steps() on PlanAndExecute, Reflexion, TreeOfThought."""
from __future__ import annotations

from typing import Any


from chimera.core.context import Context
from chimera.core.loops.plan_execute import PlanAndExecute
from chimera.core.loops.reflexion import Reflexion
from chimera.core.loops.tree_of_thought import TreeOfThought
from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall, ToolResult


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
# Tests: PlanAndExecute
# ---------------------------------------------------------------------------

class TestPlanAndExecuteIterSteps:
    def test_plan_then_execute_then_done(self) -> None:
        """Phase 1: plan (text), Phase 2: execute (tool+text)."""
        provider = MockProvider([
            text_response("Here is my plan"),        # Phase 1: plan
            tool_response("Executing step 1"),       # Phase 2: tool call
            text_response("All done"),               # Phase 2: completion
        ])
        loop = PlanAndExecute(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("do something"))

        steps = list(loop.iter_steps(provider, [EchoTool()], context, None))
        # Plan step (not done), tool step (not done), final done
        assert len(steps) == 3
        assert steps[0].done is False  # plan generated
        assert steps[1].done is False  # tool executed
        assert steps[2].done is True   # final text

    def test_run_regression(self) -> None:
        provider = MockProvider([
            text_response("Plan"),
            text_response("Done"),
        ])
        loop = PlanAndExecute(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("do it"))
        result = loop.run(provider, [EchoTool()], context, None)
        assert result.success is True

    def test_no_tools_skips_plan_phase(self) -> None:
        """Without tools, first text response is final."""
        provider = MockProvider([text_response("Answer")])
        loop = PlanAndExecute(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))
        result = loop.run(provider, [], context, None)
        assert result.success is True
        assert result.output == "Answer"


# ---------------------------------------------------------------------------
# Tests: Reflexion
# ---------------------------------------------------------------------------

class TestReflexionIterSteps:
    def test_text_only(self) -> None:
        provider = MockProvider([text_response("Hello")])
        loop = Reflexion(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))
        steps = list(loop.iter_steps(provider, [], context, None))
        assert len(steps) == 1
        assert steps[0].done is True

    def test_tool_then_text(self) -> None:
        provider = MockProvider([tool_response("I'll echo"), text_response("Done")])
        loop = Reflexion(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("echo"))
        steps = list(loop.iter_steps(provider, [EchoTool()], context, None))
        assert len(steps) == 2
        assert steps[0].done is False
        assert steps[1].done is True

    def test_run_regression(self) -> None:
        provider = MockProvider([tool_response("echo"), text_response("Done")])
        loop = Reflexion(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("echo"))
        result = loop.run(provider, [EchoTool()], context, None)
        assert result.success is True
        assert result.steps == 2


# ---------------------------------------------------------------------------
# Tests: TreeOfThought
# ---------------------------------------------------------------------------

class TestTreeOfThoughtIterSteps:
    def test_text_only_all_same(self) -> None:
        """All candidates identical — no evaluation needed."""
        provider = MockProvider([
            text_response("Answer"),
            text_response("Answer"),
            text_response("Answer"),
        ])
        loop = TreeOfThought(max_steps=10, n_candidates=3)
        context = Context(system="test")
        context.add(Message.user("hi"))
        steps = list(loop.iter_steps(provider, [], context, None))
        assert len(steps) == 1
        assert steps[0].done is True

    def test_tool_call_candidate(self) -> None:
        """First candidate with tool calls gets chosen."""
        provider = MockProvider([
            # Round 1: 3 candidates, second has tool call
            text_response("no tools"),
            tool_response("I'll use a tool", call_id="c1"),
            text_response("no tools"),
            # Round 2: 3 candidates, all same text (done)
            text_response("Done"),
            text_response("Done"),
            text_response("Done"),
        ])
        loop = TreeOfThought(max_steps=10, n_candidates=3)
        context = Context(system="test")
        context.add(Message.user("do it"))
        steps = list(loop.iter_steps(provider, [EchoTool()], context, None))
        assert len(steps) >= 2
        # First step has tool calls, second is final
        assert any(s.tool_calls for s in steps)
        assert steps[-1].done is True

    def test_run_regression(self) -> None:
        provider = MockProvider([
            text_response("Same"),
            text_response("Same"),
            text_response("Same"),
        ])
        loop = TreeOfThought(max_steps=10, n_candidates=3)
        context = Context(system="test")
        context.add(Message.user("hi"))
        result = loop.run(provider, [], context, None)
        assert result.success is True
        assert result.output == "Same"

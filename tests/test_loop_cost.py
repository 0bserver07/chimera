# tests/test_loop_cost.py
"""Tests that all loop types track costs."""
from __future__ import annotations

import tempfile

from chimera.core.context import Context
from chimera.core.loops.plan_execute import PlanAndExecute
from chimera.core.loops.reflexion import Reflexion
from chimera.core.loops.tree_of_thought import TreeOfThought
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.tools.write import WriteFileTool
from chimera.types import Message, ToolCall


class SimpleProvider(Provider):
    """Returns a plan text first, then a tool call, then finishes."""

    def __init__(self) -> None:
        self._calls = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._calls += 1
        if messages and messages[-1].role == "tool":
            return Response(content="Done.", tool_calls=[], usage={"input_tokens": 100, "output_tokens": 50})
        if self._calls == 1:
            return Response(content="Plan: write a file.", tool_calls=[], usage={"input_tokens": 200, "output_tokens": 100})
        return Response(
            content="Writing.",
            tool_calls=[ToolCall(id=f"c{self._calls}", name="write_file", arguments={"path": "f.py", "content": "x=1"})],
            usage={"input_tokens": 300, "output_tokens": 150},
        )

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "claude-sonnet-4-20250514"


class DirectProvider(Provider):
    """Returns a tool call on first call, then finishes."""

    def __init__(self) -> None:
        self._calls = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        self._calls += 1
        if messages and messages[-1].role == "tool":
            return Response(content="Done.", tool_calls=[], usage={"input_tokens": 100, "output_tokens": 50})
        return Response(
            content="Writing.",
            tool_calls=[ToolCall(id=f"c{self._calls}", name="write_file", arguments={"path": "f.py", "content": "x=1"})],
            usage={"input_tokens": 300, "output_tokens": 150},
        )

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "claude-sonnet-4-20250514"


def test_plan_execute_tracks_cost():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir)
        env.setup()
        ctx = Context(system="test")
        ctx.add(Message.user("do something"))
        result = PlanAndExecute(max_steps=10).run(
            SimpleProvider(), [WriteFileTool()], ctx, env,
        )
        assert result.cost > 0


def test_reflexion_tracks_cost():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir)
        env.setup()
        ctx = Context(system="test")
        ctx.add(Message.user("do something"))
        result = Reflexion(max_steps=10).run(
            DirectProvider(), [WriteFileTool()], ctx, env,
        )
        assert result.cost > 0


def test_tree_of_thought_tracks_cost():
    with tempfile.TemporaryDirectory() as tmpdir:
        env = LocalEnvironment(workdir=tmpdir)
        env.setup()
        ctx = Context(system="test")
        ctx.add(Message.user("do something"))
        result = TreeOfThought(max_steps=10, n_candidates=2).run(
            DirectProvider(), [WriteFileTool()], ctx, env,
        )
        assert result.cost > 0

"""Tests for ReAct.async_iter_steps() and async_drain_steps()."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chimera.core.context import Context
from chimera.core.loop import ReAct, async_drain_steps
from chimera.core.tool import BaseTool
from chimera.providers.base import Provider, Response
from chimera.types import AgentResult, Message, StepResult, ToolCall, ToolResult


# --- Mocks ---


class MockProvider(Provider):
    """Provider returning a scripted sequence of responses."""

    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return self._next()

    async def async_complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return self._next()

    def _next(self) -> Response:
        if self._call_count >= len(self._responses):
            return Response(content="(done)", tool_calls=[], usage={})
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp

    @property
    def context_window(self) -> int:
        return 200_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return "mock"


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo a message"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }

    def execute(self, args, env=None):
        return ToolResult(output=f"Echo: {args['message']}")


# --- Tests ---


class TestAsyncIterSteps:
    @pytest.mark.asyncio
    async def test_single_step_no_tools(self) -> None:
        """Provider returns text immediately — one step, done=True."""
        provider = MockProvider([
            Response(content="Hello!", tool_calls=[], usage={}),
        ])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))

        steps: list[StepResult] = []
        async for step in loop.async_iter_steps(provider, [], context, None):
            steps.append(step)

        assert len(steps) == 1
        assert steps[0].done is True
        assert steps[0].message is not None
        assert steps[0].message.content == "Hello!"

    @pytest.mark.asyncio
    async def test_tool_call_then_final(self) -> None:
        """Provider calls a tool, then gives final answer — two steps."""
        provider = MockProvider([
            Response(
                content="Calling echo",
                tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "hi"})],
                usage={},
            ),
            Response(content="Done!", tool_calls=[], usage={}),
        ])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))

        steps: list[StepResult] = []
        async for step in loop.async_iter_steps(provider, [EchoTool()], context, None):
            steps.append(step)

        assert len(steps) == 2
        assert steps[0].done is False
        assert len(steps[0].tool_calls) == 1
        assert steps[1].done is True

    @pytest.mark.asyncio
    async def test_max_steps_reached(self) -> None:
        """Loop stops after max_steps, yielding a done step."""
        # Provider always returns tool calls
        responses = [
            Response(
                content=f"step{i}",
                tool_calls=[ToolCall(id=f"tc{i}", name="echo", arguments={"message": str(i)})],
                usage={},
            )
            for i in range(10)
        ]
        provider = MockProvider(responses)
        loop = ReAct(max_steps=3)
        context = Context(system="test")
        context.add(Message.user("hi"))

        steps: list[StepResult] = []
        async for step in loop.async_iter_steps(provider, [EchoTool()], context, None):
            steps.append(step)

        # 3 tool-call steps + 1 max-steps-reached final
        assert len(steps) == 4
        assert steps[-1].done is True


class TestAsyncDrainSteps:
    @pytest.mark.asyncio
    async def test_drain_returns_agent_result(self) -> None:
        """async_drain_steps returns AgentResult from the generator."""
        provider = MockProvider([
            Response(
                content="Calling echo",
                tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "hi"})],
                usage={},
            ),
            Response(content="All done", tool_calls=[], usage={}),
        ])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))

        result = await async_drain_steps(
            loop.async_iter_steps(provider, [EchoTool()], context, None)
        )

        assert isinstance(result, AgentResult)
        assert result.success is True
        assert result.output == "All done"
        assert result.steps == 2
        assert result.tool_calls_total == 1

    @pytest.mark.asyncio
    async def test_drain_auto_denies_ask(self) -> None:
        """async_drain_steps auto-denies pending approvals (same as sync)."""
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.base import PermissionAction, PermissionPolicy

        class AskPolicy(PermissionPolicy):
            def evaluate(self, tool_name, args):
                return PermissionAction.ASK

        provider = MockProvider([
            Response(
                content="Calling echo",
                tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "hi"})],
                usage={},
            ),
            Response(content="OK denied", tool_calls=[], usage={}),
        ])
        config = LoopConfig(permissions=AskPolicy())
        loop = ReAct(max_steps=10, config=config)
        context = Context(system="test")
        context.add(Message.user("hi"))

        result = await async_drain_steps(
            loop.async_iter_steps(provider, [EchoTool()], context, None)
        )

        assert isinstance(result, AgentResult)
        # Should still succeed — the denied tool call doesn't crash


class TestAsyncRunUsesDrain:
    @pytest.mark.asyncio
    async def test_async_run_produces_same_result(self) -> None:
        """async_run (now backed by async_iter_steps) produces correct result."""
        provider = MockProvider([
            Response(
                content="Calling echo",
                tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "hello"})],
                usage={},
            ),
            Response(content="Final answer", tool_calls=[], usage={}),
        ])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))

        result = await loop.async_run(provider, [EchoTool()], context, None)

        assert result.success is True
        assert result.output == "Final answer"
        assert result.steps == 2
        assert result.tool_calls_total == 1

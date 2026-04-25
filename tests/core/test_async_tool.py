"""Tests for BaseTool.async_execute and _FunctionTool.async_execute."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from chimera.core.tool import BaseTool, tool
from chimera.types import ToolResult


class SyncTool(BaseTool):
    """A tool with only sync execute."""

    name = "sync_tool"
    description = "A sync-only tool"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    def execute(self, args: dict[str, Any], env=None) -> ToolResult:
        return ToolResult(output=f"sync:{args['msg']}")


class NativeAsyncTool(BaseTool):
    """A tool with native async execute."""

    name = "async_tool"
    description = "A native async tool"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    def execute(self, args: dict[str, Any], env=None) -> ToolResult:
        return ToolResult(output=f"sync:{args['msg']}")

    async def async_execute(self, args: dict[str, Any], env=None) -> ToolResult:
        await asyncio.sleep(0)  # Prove we're truly async
        return ToolResult(output=f"async:{args['msg']}")


class TestAsyncExecuteDefault:
    @pytest.mark.asyncio
    async def test_default_wraps_sync(self) -> None:
        """Default async_execute delegates to sync execute via executor."""
        t = SyncTool()
        result = await t.async_execute({"msg": "hello"}, None)
        assert result.output == "sync:hello"
        assert result.success

    @pytest.mark.asyncio
    async def test_native_async_override(self) -> None:
        """Subclass can override async_execute for native async."""
        t = NativeAsyncTool()
        result = await t.async_execute({"msg": "hello"}, None)
        assert result.output == "async:hello"
        assert result.success


class TestFunctionToolAsync:
    @pytest.mark.asyncio
    async def test_decorator_tool_async(self) -> None:
        """@tool decorator tools get async_execute via default wrapper."""

        @tool(
            name="greet",
            description="Say hello",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        )
        def greet(args: dict[str, Any], env=None) -> ToolResult:
            return ToolResult(output=f"Hi {args['name']}")

        result = await greet.async_execute({"name": "World"}, None)
        assert result.output == "Hi World"


class TestConcurrentAsyncExecution:
    @pytest.mark.asyncio
    async def test_multiple_tools_concurrent(self) -> None:
        """Multiple async_execute calls run concurrently."""

        async def run_tool(t: BaseTool, msg: str) -> ToolResult:
            return await t.async_execute({"msg": msg}, None)

        t = SyncTool()
        results = await asyncio.gather(
            run_tool(t, "a"),
            run_tool(t, "b"),
            run_tool(t, "c"),
        )
        assert [r.output for r in results] == ["sync:a", "sync:b", "sync:c"]


# -- Async Tool Executor Tests --

from chimera.core.context import Context
from chimera.core.tool_executor import (
    async_execute_tool_calls_incremental,
)
from chimera.types import Message, ToolCall


class TestAsyncToolExecutor:
    @pytest.mark.asyncio
    async def test_concurrent_execution(self) -> None:
        """Tool calls execute concurrently via asyncio.gather."""
        t = SyncTool()
        tool_map = {"sync_tool": t}
        context = Context(system="test")
        context.add(Message.user("hi"))
        context.add(
            Message.assistant(
                "calling tools",
                tool_calls=[
                    ToolCall(id="tc1", name="sync_tool", arguments={"msg": "a"}),
                    ToolCall(id="tc2", name="sync_tool", arguments={"msg": "b"}),
                ],
            ),
        )

        result = await async_execute_tool_calls_incremental(
            [
                ToolCall(id="tc1", name="sync_tool", arguments={"msg": "a"}),
                ToolCall(id="tc2", name="sync_tool", arguments={"msg": "b"}),
            ],
            tool_map,
            context,
            None,
            None,
        )
        assert result.executed == 2
        assert len(result.results) == 2
        assert result.results[0].output == "sync:a"
        assert result.results[1].output == "sync:b"

    @pytest.mark.asyncio
    async def test_unknown_tool_skipped(self) -> None:
        """Unknown tool names produce error messages, not crashes."""
        tool_map: dict[str, BaseTool] = {}
        context = Context(system="test")
        context.add(Message.user("hi"))
        context.add(Message.assistant("calling", tool_calls=[
            ToolCall(id="tc1", name="missing", arguments={}),
        ]))

        result = await async_execute_tool_calls_incremental(
            [ToolCall(id="tc1", name="missing", arguments={})],
            tool_map,
            context,
            None,
            None,
        )
        assert result.executed == 1  # counted even though unknown
        assert result.pending is None

    @pytest.mark.asyncio
    async def test_results_ordered(self) -> None:
        """Results maintain tool_calls order regardless of completion order."""

        class SlowTool(BaseTool):
            name = "slow"
            description = "slow"
            parameters: dict[str, Any] = {
                "type": "object",
                "properties": {"delay": {"type": "number"}},
                "required": ["delay"],
            }

            def execute(self, args, env=None):
                return ToolResult(output=f"done:{args['delay']}")

            async def async_execute(self, args, env=None):
                await asyncio.sleep(args["delay"])
                return ToolResult(output=f"done:{args['delay']}")

        tool_map = {"slow": SlowTool()}
        context = Context(system="test")
        context.add(Message.user("hi"))
        context.add(Message.assistant("calling", tool_calls=[
            ToolCall(id="tc1", name="slow", arguments={"delay": 0.05}),
            ToolCall(id="tc2", name="slow", arguments={"delay": 0.01}),
        ]))

        result = await async_execute_tool_calls_incremental(
            [
                ToolCall(id="tc1", name="slow", arguments={"delay": 0.05}),
                ToolCall(id="tc2", name="slow", arguments={"delay": 0.01}),
            ],
            tool_map,
            context,
            None,
            None,
        )
        assert result.results[0].output == "done:0.05"
        assert result.results[1].output == "done:0.01"

    @pytest.mark.asyncio
    async def test_permission_ask_pauses(self) -> None:
        """ASK permission pauses execution and returns pending."""
        from chimera.permissions.base import PermissionPolicy, PermissionAction
        from chimera.core.loop_config import LoopConfig

        class AskPolicy(PermissionPolicy):
            def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
                return PermissionAction.ASK

        t = SyncTool()
        t.requires_approval = True
        tool_map = {"sync_tool": t}
        config = LoopConfig(permissions=AskPolicy())
        context = Context(system="test")
        context.add(Message.user("hi"))
        context.add(Message.assistant("calling", tool_calls=[
            ToolCall(id="tc1", name="sync_tool", arguments={"msg": "a"}),
        ]))

        result = await async_execute_tool_calls_incremental(
            [ToolCall(id="tc1", name="sync_tool", arguments={"msg": "a"})],
            tool_map,
            context,
            None,
            config,
        )
        assert result.pending is not None
        assert result.executed == 0

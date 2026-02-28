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

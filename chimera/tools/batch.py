"""BatchTool — execute multiple tool calls in parallel."""
from __future__ import annotations

import asyncio
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult


class BatchTool(BaseTool):
    """Execute multiple tool calls in parallel."""

    name = "batch"
    description = "Execute multiple tool calls in parallel"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string"},
                        "arguments": {"type": "object"},
                    },
                },
            },
        },
        "required": ["calls"],
    }
    is_concurrency_safe = True
    max_concurrent = 25

    def __init__(self, tool_map: dict[str, BaseTool] | None = None) -> None:
        self._tools = tool_map or {}

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        """Synchronous fallback — runs tools sequentially."""
        results: list[str] = []
        for call in args.get("calls", [])[:self.max_concurrent]:
            tool = self._tools.get(call.get("tool"))
            if tool:
                r = tool.execute(call.get("arguments", {}), env)
                results.append(f"[{call['tool']}] {r.output[:500]}")
            else:
                results.append(f"[{call.get('tool')}] Unknown tool")
        return ToolResult(output="\n".join(results))

    async def async_execute(
        self, args: dict[str, Any], env: Environment | None,
    ) -> ToolResult:
        """Async execution — runs tools in parallel via asyncio.gather."""
        calls = args.get("calls", [])[:self.max_concurrent]
        tasks: list[tuple[dict[str, Any], Any]] = []

        for call in calls:
            tool = self._tools.get(call.get("tool"))
            if tool and hasattr(tool, "async_execute"):
                tasks.append((call, tool.async_execute(call.get("arguments", {}), env)))
            elif tool:
                tasks.append((call, asyncio.to_thread(tool.execute, call.get("arguments", {}), env)))
            else:
                tasks.append((call, None))

        results: list[str] = []
        coros = [t[1] for t in tasks if t[1] is not None]
        if coros:
            done = await asyncio.gather(*coros, return_exceptions=True)
            j = 0
            for call, coro in tasks:
                if coro is None:
                    results.append(f"[{call.get('tool')}] Unknown tool")
                elif isinstance(done[j], Exception):
                    results.append(f"[{call.get('tool')}] ERROR: {done[j]}")
                    j += 1
                else:
                    results.append(f"[{call.get('tool')}] {done[j].output[:500]}")  # type: ignore[union-attr]
                    j += 1
        else:
            for call, _ in tasks:
                results.append(f"[{call.get('tool')}] Unknown tool")

        return ToolResult(output="\n".join(results))

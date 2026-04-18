"""StreamingToolExecutor: concurrent tool execution with concurrency control.

Manages submission of tool calls as asyncio Tasks, respecting the
``is_concurrency_safe`` flag on each tool.  Non-safe tools drain all
pending work before starting, ensuring they run in isolation.

When a tool named ``"bash"`` fails, the executor fires its sibling-abort
signal so other in-flight tasks can observe the failure and short-circuit.
"""
from __future__ import annotations

import asyncio

from chimera.core.abort import AbortSignal
from chimera.core.tool import BaseTool
from chimera.types import ToolCall, ToolResult

__all__ = ["StreamingToolExecutor"]


class StreamingToolExecutor:
    """Manages concurrent execution of tool calls with per-tool safety flags.

    Args:
        tools: List of :class:`~chimera.core.tool.BaseTool` instances.
        max_concurrent: Upper bound on simultaneous asyncio Tasks.
            Currently used as a capacity hint; enforcement is left to
            future semaphore work.
    """

    def __init__(self, tools: list[BaseTool], max_concurrent: int = 5) -> None:
        self.tool_map: dict[str, BaseTool] = {t.name: t for t in tools}
        self.max_concurrent = max_concurrent

        self._pending: list[asyncio.Task[None]] = []
        self._results: dict[str, ToolResult] = {}
        self._submitted_order: list[ToolCall] = []
        self._sibling_abort: AbortSignal = AbortSignal()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(self, tool_call: ToolCall) -> None:
        """Submit *tool_call* for execution.

        If the resolved tool is concurrency-safe, the task is created
        immediately (up to *max_concurrent*).  Otherwise all pending tasks
        are awaited first so the non-safe tool runs in isolation.
        """
        self._submitted_order.append(tool_call)

        tool = self.tool_map.get(tool_call.name)

        if tool is not None and tool.is_concurrency_safe:
            task = asyncio.create_task(self._execute(tool, tool_call))
            self._pending.append(task)
        else:
            # Drain existing work before executing non-concurrent tool.
            await self._drain_pending()
            if tool is None:
                # Unknown tool — store synthetic error immediately.
                self._results[tool_call.id] = ToolResult(
                    output="", error=f"Unknown tool: {tool_call.name}"
                )
                return
            # Create task but leave it pending; caller uses collect() or discard().
            task = asyncio.create_task(self._execute(tool, tool_call))
            self._pending.append(task)

    async def collect(self) -> list[tuple[ToolCall, ToolResult]]:
        """Await all pending tasks and return results in submission order."""
        await self._drain_pending()
        return self._ordered_results()

    async def discard(self) -> list[tuple[ToolCall, ToolResult]]:
        """Cancel pending tasks and return synthetic errors for unfinished ones.

        Tasks that already completed have their real results preserved.
        Unfinished tasks get a synthetic ``ToolResult(error="cancelled")``.
        """
        for task in self._pending:
            if not task.done():
                task.cancel()

        # Give the event loop a chance to process cancellations.
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

        # Fill in synthetic errors for any call whose result was not stored.
        for call in self._submitted_order:
            if call.id not in self._results:
                self._results[call.id] = ToolResult(
                    output="", error="cancelled"
                )

        self._pending.clear()
        return self._ordered_results()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute(self, tool: BaseTool, call: ToolCall) -> None:
        """Execute *tool* with *call*'s arguments and store the result."""
        try:
            result = await tool.async_execute(call.arguments, None)
        except asyncio.CancelledError:
            self._results[call.id] = ToolResult(output="", error="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(output="", error=str(exc))

        self._results[call.id] = result

        # Bash error cascading: abort siblings when bash fails.
        if call.name == "bash" and not result.success:
            self._sibling_abort.abort(f"bash failed: {result.error}")

    async def _drain_pending(self) -> None:
        """Await all currently pending tasks (clears the pending list)."""
        if not self._pending:
            return
        await asyncio.gather(*self._pending, return_exceptions=True)
        self._pending.clear()

    def _ordered_results(self) -> list[tuple[ToolCall, ToolResult]]:
        """Return (ToolCall, ToolResult) pairs in submission order."""
        return [
            (call, self._results[call.id])
            for call in self._submitted_order
            if call.id in self._results
        ]

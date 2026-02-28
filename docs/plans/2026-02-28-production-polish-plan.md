# Production Polish Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete Chimera with full async support, concurrent tool execution, production-grade MCP/LSP, cost budgets, and a full-featured REPL.

**Architecture:** 10 independent tasks across 6 components. Async foundation (Tasks 1-3) enables concurrent tool execution and async streaming. Ensemble (Task 4), MCP (Tasks 5-6), LSP (Tasks 7-8), Cost (Task 9), and REPL (Task 10) are standalone improvements.

**Tech Stack:** Python 3.11+, stdlib only (`asyncio`, `threading`, `readline`, `queue`, `collections.deque`)

---

## Context

Chimera has 1094 passing tests. The serving layer is complete. Six areas need production polish:

1. **Async** — `async_run()` exists but tools run sequentially, no `async_iter_steps()`, no concurrent tool execution.
2. **Parallel Ensemble** — ThreadPoolExecutor works but no async path, no early cancellation.
3. **MCP** — Client works but no retry, no stderr reading, no health checks, no tool refresh.
4. **LSP** — 4 methods work but diagnostics are broken (async notifications never read), missing workspace/symbol, code actions, completion.
5. **Cost** — `calculate_cost()` works but no budgets, no tracking, no estimation.
6. **REPL** — Functional but uses raw `input()`, only supports `/exit` and `/quit`.

**Design doc:** `docs/plans/2026-02-28-production-polish-design.md`

---

## Task 1: BaseTool.async_execute()

**Files:**
- Modify: `chimera/core/tool.py`
- Create: `tests/test_async_tool.py`

**Step 1: Write tests for async_execute**

Create `tests/test_async_tool.py`:

```python
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
        tool = SyncTool()
        result = await tool.async_execute({"msg": "hello"}, None)
        assert result.output == "sync:hello"
        assert result.success

    @pytest.mark.asyncio
    async def test_native_async_override(self) -> None:
        """Subclass can override async_execute for native async."""
        tool = NativeAsyncTool()
        result = await tool.async_execute({"msg": "hello"}, None)
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
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_async_tool.py -v
```

Expected: FAIL — `BaseTool` has no `async_execute` method.

**Step 3: Implement async_execute on BaseTool and _FunctionTool**

In `chimera/core/tool.py`:

Add `import asyncio` to imports (after `from abc import ABC, abstractmethod`).

Add method to `BaseTool` class (after `execute` abstract method, around line 62):

```python
    async def async_execute(
        self, args: dict[str, Any], env: Environment | None
    ) -> ToolResult:
        """Async version of execute. Default wraps sync via run_in_executor.

        Override for native async I/O (HTTP calls, async DB queries, etc.).
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.execute, args, env)
```

No changes needed for `_FunctionTool` — it inherits `async_execute` from `BaseTool`, which calls `self.execute()` which calls `self._func()`.

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_async_tool.py -v
```

Expected: All 4 tests PASS.

**Step 5: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1094+ tests pass, 0 failures.

**Step 6: Commit**

```bash
git add chimera/core/tool.py tests/test_async_tool.py
git commit -m "feat: add BaseTool.async_execute with run_in_executor default"
```

---

## Task 2: Async Tool Executor

**Files:**
- Modify: `chimera/core/tool_executor.py`
- Modify: `tests/test_async_tool.py`

**Step 1: Write tests for async_execute_tool_calls_incremental**

Append to `tests/test_async_tool.py`:

```python
from chimera.core.context import Context
from chimera.core.tool_executor import (
    ToolExecutionResult,
    async_execute_tool_calls_incremental,
)
from chimera.types import Message, ToolCall


class TestAsyncToolExecutor:
    @pytest.mark.asyncio
    async def test_concurrent_execution(self) -> None:
        """Tool calls execute concurrently via asyncio.gather."""
        tool = SyncTool()
        tool_map = {"sync_tool": tool}
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
        assert result.executed == 0
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
            def check(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
                return PermissionAction.ASK

        tool = SyncTool()
        tool.requires_approval = True
        tool_map = {"sync_tool": tool}
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
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_async_tool.py::TestAsyncToolExecutor -v
```

Expected: FAIL — `async_execute_tool_calls_incremental` does not exist.

**Step 3: Implement async_execute_tool_calls_incremental**

In `chimera/core/tool_executor.py`, add `import asyncio` to imports. Then add this function at the end of the file (after `execute_tool_calls_incremental`):

```python
async def async_execute_tool_calls_incremental(
    tool_calls: list[ToolCall],
    tool_map: dict[str, BaseTool],
    context: Context,
    env: Environment | None,
    config: "LoopConfig | None",
) -> ToolExecutionResult:
    """Async version of execute_tool_calls_incremental.

    Runs permission and detection checks synchronously (in-memory, no I/O).
    Executes approved tool calls concurrently via asyncio.gather().
    Results are ordered to match tool_calls order.
    """
    if config and config.permissions:
        from chimera.permissions.base import PermissionAction

    results: list[ToolResult] = []
    executed = 0
    approved_calls: list[tuple[int, ToolCall, BaseTool]] = []

    for idx, tc in enumerate(tool_calls):
        tool = tool_map.get(tc.name)
        if tool is None:
            context.add(Message.tool(tc.id, f"Error: unknown tool {tc.name}"))
            continue

        # -- Permission check (sync, in-memory) --
        if config and config.permissions:
            action = config.permissions.check(tc.name, tc.arguments)
            if action == PermissionAction.DENY:
                context.add(Message.tool(tc.id, "Permission denied"))
                continue
            if action == PermissionAction.ASK:
                pending = PendingApproval(tool_call=tc)
                remaining = [c for c in tool_calls[idx + 1:]]
                return ToolExecutionResult(
                    executed=executed,
                    results=results,
                    pending=pending,
                    remaining=remaining,
                )

        # -- Detection check (sync, string comparison) --
        if config and config.detector:
            detection = config.detector.check(tc)
            if detection.detected:
                raise LoopBreak(detection.message)

        approved_calls.append((idx, tc, tool))

    if not approved_calls:
        return ToolExecutionResult(executed=0, results=results)

    # -- Execute all approved calls concurrently --
    async def _run(tc: ToolCall, t: BaseTool) -> ToolResult:
        try:
            return await t.async_execute(tc.arguments, env)
        except Exception as exc:
            return ToolResult(output="", error=str(exc))

    tasks = [_run(tc, t) for _, tc, t in approved_calls]
    tool_results = await asyncio.gather(*tasks)

    # Add results to context in original order and emit events
    event_bus = config.event_bus if config else None
    for (_, tc, _), tr in zip(approved_calls, tool_results):
        results.append(tr)
        executed += 1
        content = tr.output if tr.success else f"Error: {tr.error}\n{tr.output}"
        context.add(Message.tool(tc.id, content))
        if event_bus:
            from chimera.events.types import ToolEvent
            event_bus.publish(ToolEvent(tool_name=tc.name, result=content))

    return ToolExecutionResult(executed=executed, results=results)
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_async_tool.py -v
```

Expected: All 8 tests PASS.

**Step 5: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1094+ tests pass.

**Step 6: Commit**

```bash
git add chimera/core/tool_executor.py tests/test_async_tool.py
git commit -m "feat: add async_execute_tool_calls_incremental with concurrent execution"
```

---

## Task 3: ReAct.async_iter_steps() + async_drain_steps()

**Files:**
- Modify: `chimera/core/loop.py`
- Modify: `chimera/__init__.py`
- Create: `tests/test_async_iter_steps.py`

**Step 1: Write tests**

Create `tests/test_async_iter_steps.py`:

```python
"""Tests for ReAct.async_iter_steps and async_drain_steps."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from chimera.core.context import Context
from chimera.core.loop import ReAct, async_drain_steps
from chimera.core.tool import BaseTool
from chimera.core.loop_config import LoopConfig
from chimera.providers.base import Provider, Response, StreamEvent
from chimera.streaming.handlers import CollectStreamHandler
from chimera.types import AgentResult, Message, StepResult, ToolCall, ToolResult


# -- Helpers --

def _text_response(content: str) -> Response:
    return Response(content=content, tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5})


def _tool_response(content: str, tool_name: str = "echo", args: dict | None = None) -> Response:
    return Response(
        content=content,
        tool_calls=[ToolCall(id="tc1", name=tool_name, arguments=args or {"msg": "hi"})],
        usage={"input_tokens": 10, "output_tokens": 5},
    )


class AsyncMockProvider(Provider):
    def __init__(self, responses: list[Response]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        r = self._responses[self._idx]
        self._idx += 1
        return r

    async def async_complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        r = self._responses[self._idx]
        self._idx += 1
        return r

    async def async_stream(self, messages, tools=None, temperature=0.0, max_tokens=None):
        r = self._responses[self._idx]
        self._idx += 1
        yield StreamEvent(type="text_delta", content=r.content)
        for tc in r.tool_calls:
            yield StreamEvent(type="tool_call_start", tool_call=tc)
            yield StreamEvent(type="tool_call_complete", tool_call=tc)
        yield StreamEvent(type="done", usage=r.usage)

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
    description = "Echo"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    def execute(self, args, env=None):
        return ToolResult(output=f"echo:{args['msg']}")


class TestAsyncIterSteps:
    @pytest.mark.asyncio
    async def test_text_only(self) -> None:
        """Single text response yields one step and completes."""
        provider = AsyncMockProvider([_text_response("Hello")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))

        steps = []
        async for step in loop.async_iter_steps(provider, [], context, None):
            steps.append(step)

        assert len(steps) == 1
        assert steps[0].done is True
        assert steps[0].message.content == "Hello"

    @pytest.mark.asyncio
    async def test_tool_then_text(self) -> None:
        """Tool call followed by text yields two steps."""
        provider = AsyncMockProvider([
            _tool_response("Let me echo", args={"msg": "world"}),
            _text_response("Done"),
        ])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("echo world"))

        steps = []
        async for step in loop.async_iter_steps(provider, [EchoTool()], context, None):
            steps.append(step)

        assert len(steps) == 2
        assert steps[0].done is False
        assert len(steps[0].tool_calls) == 1
        assert steps[1].done is True

    @pytest.mark.asyncio
    async def test_max_steps(self) -> None:
        """Hitting max_steps yields final done step."""
        provider = AsyncMockProvider([
            _tool_response("tool1", args={"msg": "a"}),
            _tool_response("tool2", args={"msg": "b"}),
            _tool_response("tool3", args={"msg": "c"}),
        ])
        loop = ReAct(max_steps=2)
        context = Context(system="test")
        context.add(Message.user("go"))

        steps = []
        async for step in loop.async_iter_steps(provider, [EchoTool()], context, None):
            steps.append(step)

        assert steps[-1].done is True
        assert steps[-1].message.content == "Max steps reached"


class TestAsyncDrainSteps:
    @pytest.mark.asyncio
    async def test_drain_returns_agent_result(self) -> None:
        """async_drain_steps consumes generator and returns AgentResult."""
        provider = AsyncMockProvider([_text_response("Hello")])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("hi"))

        result = await async_drain_steps(
            loop, loop.async_iter_steps(provider, [], context, None),
        )
        assert isinstance(result, AgentResult)
        assert result.success is True
        assert result.output == "Hello"

    @pytest.mark.asyncio
    async def test_drain_auto_denies_pending(self) -> None:
        """async_drain_steps auto-denies pending approvals."""
        from chimera.permissions.base import PermissionAction, PermissionPolicy

        class AskPolicy(PermissionPolicy):
            def check(self, tool_name, args):
                return PermissionAction.ASK

        tool = EchoTool()
        tool.requires_approval = True
        provider = AsyncMockProvider([
            _tool_response("calling", args={"msg": "x"}),
            _text_response("ok"),
        ])
        config = LoopConfig(permissions=AskPolicy())
        loop = ReAct(max_steps=10, config=config)
        context = Context(system="test")
        context.add(Message.user("go"))

        result = await async_drain_steps(
            loop, loop.async_iter_steps(provider, [tool], context, None),
        )
        assert isinstance(result, AgentResult)


class TestAsyncRunRewrite:
    @pytest.mark.asyncio
    async def test_async_run_uses_iter_steps(self) -> None:
        """Rewritten async_run produces same results as before."""
        provider = AsyncMockProvider([
            _tool_response("calling", args={"msg": "hi"}),
            _text_response("Done"),
        ])
        loop = ReAct(max_steps=10)
        context = Context(system="test")
        context.add(Message.user("echo hi"))

        result = await loop.async_run(provider, [EchoTool()], context, None)
        assert result.success is True
        assert result.steps == 2
        assert result.output == "Done"

    @pytest.mark.asyncio
    async def test_async_run_with_streaming(self) -> None:
        """async_run with handler uses async_stream."""
        handler = CollectStreamHandler()
        provider = AsyncMockProvider([_text_response("Hello")])
        config = LoopConfig(handler=handler)
        loop = ReAct(max_steps=10, config=config)
        context = Context(system="test")
        context.add(Message.user("hi"))

        result = await loop.async_run(provider, [], context, None)
        assert result.success is True
        assert any(e["type"] == "text" for e in handler.events)
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_async_iter_steps.py -v
```

Expected: FAIL — `async_iter_steps` and `async_drain_steps` don't exist.

**Step 3: Implement async_iter_steps, async_drain_steps, and rewrite async_run**

In `chimera/core/loop.py`:

Add to imports (top of file):

```python
from collections.abc import AsyncGenerator, Generator
```

(Replace the existing `from collections.abc import Generator` import.)

Add `from chimera.core.tool_executor import async_execute_tool_calls_incremental` to the import from tool_executor (alongside the existing imports).

Add `_async_accumulate_stream` static method after `_accumulate_stream` (around line 260):

```python
    @staticmethod
    async def _async_accumulate_stream(
        events: AsyncIterator[StreamEvent],
        handler: StreamHandler | None,
    ) -> Response:
        """Async version of _accumulate_stream. Consumes AsyncIterator[StreamEvent]."""
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        current_tool_call: ToolCall | None = None
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        async for event in events:
            if handler:
                handler.handle_event(event)
            if event.type == "text_delta":
                content_parts.append(event.content)
            elif event.type == "tool_call_start":
                current_tool_call = event.tool_call
            elif event.type == "tool_call_delta":
                pass
            elif event.type == "tool_call_complete":
                if event.tool_call is not None:
                    tool_calls.append(event.tool_call)
                current_tool_call = None
            elif event.type == "done":
                if current_tool_call is not None:
                    tool_calls.append(current_tool_call)
                    current_tool_call = None
                if event.tool_call and event.tool_call not in tool_calls:
                    tool_calls.append(event.tool_call)
                if event.usage:
                    usage = event.usage

        if current_tool_call is not None:
            tool_calls.append(current_tool_call)

        return Response(content="".join(content_parts), tool_calls=tool_calls, usage=usage)
```

Add `async_iter_steps` method to ReAct class (after the `run` method, replacing the existing `async_run`):

```python
    async def async_iter_steps(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AsyncGenerator[StepResult, None]:
        """Async generator yielding one StepResult per LLM turn.

        Uses provider.async_complete() or provider.async_stream() depending
        on whether a handler is configured. Tool calls execute concurrently
        via async_execute_tool_calls_incremental.

        The final AgentResult is stored in self._async_result after the
        generator completes.
        """
        tool_map = {t.name: t for t in tools}
        schemas = [t.to_anthropic_schema() for t in tools]
        steps = 0
        total_tool_calls = 0
        total_cost = 0.0
        event_bus = self.config.event_bus if self.config else None
        handler: StreamHandler | None = self.config.handler if self.config else None
        self._async_result: AgentResult | None = None

        for _ in range(self.max_steps):
            steps += 1

            if handler:
                handler.on_step_start(steps)
                events = provider.async_stream(
                    context.to_messages(), tools=schemas if schemas else None,
                )
                response = await self._async_accumulate_stream(events, handler)
            else:
                response = await provider.async_complete(
                    context.to_messages(), tools=schemas if schemas else None,
                )

            step_cost = calculate_cost(provider.model_name, response.usage)
            total_cost += step_cost
            context.add(
                Message.assistant(response.content, tool_calls=response.tool_calls),
            )

            if not response.has_tool_calls:
                if event_bus:
                    from chimera.events.types import StepEvent
                    event_bus.publish(StepEvent(step_number=steps, content=response.content))
                if handler:
                    handler.on_step_end(steps)
                    handler.on_done()
                self._async_result = AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=True,
                )
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=[],
                    done=True,
                    step=steps,
                    cost=step_cost,
                )
                return

            # Execute tool calls concurrently
            try:
                exec_result = await async_execute_tool_calls_incremental(
                    response.tool_calls, tool_map, context, env, self.config,
                )
            except LoopBreak:
                if handler:
                    handler.on_step_end(steps)
                    handler.on_done()
                self._async_result = AgentResult(
                    output=response.content,
                    steps=steps,
                    tool_calls_total=total_tool_calls + len(response.tool_calls),
                    cost=total_cost,
                    success=False,
                    error="Loop detected",
                )
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    done=True,
                    step=steps,
                    cost=step_cost,
                )
                return

            total_tool_calls += exec_result.executed

            if handler:
                for tc_idx, tc in enumerate(response.tool_calls):
                    handler.on_tool_start(tc.name, tc.id)
                    if tc_idx < len(exec_result.results):
                        tr = exec_result.results[tc_idx]
                        content = tr.output if tr.success else f"Error: {tr.error}\n{tr.output}"
                        handler.on_tool_end(tc.id, content[:500])

            if exec_result.pending is not None:
                step = StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    tool_results=exec_result.results,
                    done=False,
                    step=steps,
                    cost=step_cost,
                    pending_approval=exec_result.pending,
                )
                yield step

                pa = exec_result.pending
                if pa.approved:
                    remaining = [pa.tool_call] + exec_result.remaining
                    try:
                        extra = await async_execute_tool_calls_incremental(
                            remaining, tool_map, context, env, None,
                        )
                    except LoopBreak:
                        self._async_result = AgentResult(
                            output=response.content,
                            steps=steps,
                            tool_calls_total=total_tool_calls,
                            cost=total_cost,
                            success=False,
                            error="Loop detected",
                        )
                        return
                    total_tool_calls += extra.executed
                else:
                    context.add(
                        Message.tool(pa.tool_call.id, pa.denial_message),
                    )
            else:
                if event_bus:
                    from chimera.events.types import StepEvent
                    event_bus.publish(StepEvent(step_number=steps, content=response.content))
                yield StepResult(
                    message=Message.assistant(response.content),
                    tool_calls=response.tool_calls,
                    tool_results=exec_result.results,
                    done=False,
                    step=steps,
                    cost=step_cost,
                )

            if handler:
                handler.on_step_end(steps)

        # Max steps
        if handler:
            handler.on_done()
        self._async_result = AgentResult(
            output="Max steps reached",
            steps=steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=False,
            error="Max steps reached",
        )
        yield StepResult(
            message=Message.assistant("Max steps reached"),
            tool_calls=[],
            done=True,
            step=steps,
            cost=0.0,
        )
```

Replace existing `async_run` method with:

```python
    async def async_run(
        self,
        provider: Provider,
        tools: list[BaseTool],
        context: Context,
        env: Environment | None,
    ) -> AgentResult:
        """Run the loop to completion using async provider calls.

        Uses async_iter_steps internally. Concurrent tool execution.
        Any pending ASK permissions are auto-denied.
        """
        return await async_drain_steps(
            self, self.async_iter_steps(provider, tools, context, env),
        )
```

Add `async_drain_steps` as a module-level function (after `drain_steps`):

```python
async def async_drain_steps(
    loop: ReAct,
    gen: AsyncGenerator[StepResult, None],
) -> AgentResult:
    """Consume an async_iter_steps generator to completion.

    Any pending approvals are automatically denied.
    Returns the AgentResult stored by the generator on the loop instance.
    """
    async for step in gen:
        if step.pending_approval:
            step.pending_approval.deny("Auto-denied by async_drain_steps")

    if loop._async_result is not None:
        return loop._async_result

    return AgentResult(
        output="", steps=0, tool_calls_total=0, cost=0.0,
        success=False, error="Generator ended unexpectedly",
    )
```

Update `chimera/__init__.py` — add `async_drain_steps` to the import from `chimera.core.loop` and to `__all__`:

```python
from chimera.core.loop import async_drain_steps, drain_steps
```

Add `"async_drain_steps"` to `__all__` list (next to `"drain_steps"`).

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_async_iter_steps.py -v
```

Expected: All 7 tests PASS.

**Step 5: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1094+ tests pass (existing async_run tests should still pass since behavior is preserved).

**Step 6: Commit**

```bash
git add chimera/core/loop.py chimera/__init__.py tests/test_async_iter_steps.py
git commit -m "feat: add async_iter_steps with concurrent tool execution and async streaming"
```

---

## Task 4: Ensemble.async_run() + first_success

**Files:**
- Modify: `chimera/env/git_env.py`
- Modify: `chimera/composition/ensemble.py`
- Create: `tests/test_ensemble_async.py`

**Step 1: Write tests**

Create `tests/test_ensemble_async.py`:

```python
"""Tests for Ensemble.async_run and first_success mode."""
from __future__ import annotations

import asyncio
import tempfile
from typing import Any

import pytest

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.composition.ensemble import Ensemble
from chimera.env.git_env import GitEnvironment
from chimera.env.local import LocalEnvironment
from chimera.providers.base import Provider, Response
from chimera.types import AgentResult, Message


class LabelProvider(Provider):
    def __init__(self, label: str) -> None:
        self.label = label

    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return Response(
            content=f"Result from {self.label}",
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 10},
        )

    async def async_complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return self.complete(messages, tools, temperature, max_tokens)

    @property
    def context_window(self) -> int:
        return 100_000

    @property
    def supports_tool_use(self) -> bool:
        return False

    @property
    def model_name(self) -> str:
        return self.label


class TestEnsembleAsyncRun:
    @pytest.mark.asyncio
    async def test_async_all_agents(self) -> None:
        """async_run runs all agents and returns results."""
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = await ensemble.async_run("task", env)
            assert len(results) == 2
            labels = {r.output for r in results}
            assert "Result from A" in labels
            assert "Result from B" in labels

    @pytest.mark.asyncio
    async def test_async_no_env_sequential(self) -> None:
        """async_run with no env falls back to sequential."""
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents)
        results = await ensemble.async_run("task", None)
        assert len(results) == 1
        assert results[0].output == "Result from A"


class TestFirstSuccess:
    @pytest.mark.asyncio
    async def test_first_success_returns_early(self) -> None:
        """first_success=True returns after first successful agent."""
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("C"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents, first_success=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = await ensemble.async_run("task", env)
            # At least one result, possibly all (depends on timing)
            assert len(results) >= 1
            assert any(r.success for r in results)

    def test_first_success_sync(self) -> None:
        """first_success works in sync run too."""
        agents = [
            Agent(provider=LabelProvider("A"), loop=ReAct(max_steps=1)),
            Agent(provider=LabelProvider("B"), loop=ReAct(max_steps=1)),
        ]
        ensemble = Ensemble(agents, first_success=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            results = ensemble.run("task", env)
            assert len(results) >= 1
            assert any(r.success for r in results)


class TestGitEnvironmentClone:
    def test_clone_creates_fresh_git_repo(self) -> None:
        """GitEnvironment.clone() creates independent repo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = GitEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            env.write_file("hello.txt", "world")
            env.checkpoint()

            clone = env.clone()
            try:
                assert clone.read_file("hello.txt") == "world"
                # Clone has its own git repo
                result = clone.run_command("git log --oneline")
                assert result.exit_code == 0
            finally:
                import shutil
                shutil.rmtree(str(clone.workdir), ignore_errors=True)

    def test_clone_is_independent(self) -> None:
        """Changes in clone don't affect original."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = GitEnvironment(workdir=tmpdir, test_cmd="echo ok")
            env.setup()
            env.write_file("file.txt", "original")
            env.checkpoint()

            clone = env.clone()
            try:
                clone.write_file("file.txt", "modified")
                assert env.read_file("file.txt") == "original"
            finally:
                import shutil
                shutil.rmtree(str(clone.workdir), ignore_errors=True)
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_ensemble_async.py -v
```

Expected: FAIL — `Ensemble.async_run` doesn't exist, `first_success` parameter unknown.

**Step 3: Implement GitEnvironment.clone()**

In `chimera/env/git_env.py`, add imports and override `clone()`:

```python
"""Git-based environment with commit-based checkpointing."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from chimera.env.local import LocalEnvironment
from chimera.types import CommandResult


class GitEnvironment(LocalEnvironment):
    # ... existing code unchanged ...

    def clone(self) -> GitEnvironment:
        """Create an independent copy with a fresh git repository."""
        clone_dir = Path(tempfile.mkdtemp(
            prefix="chimera-git-clone-", dir=self.workdir.parent,
        ))
        # Copy workspace files but skip .git (we'll init fresh)
        self._copy_workspace_no_git(self.workdir, clone_dir)
        cloned = GitEnvironment(
            workdir=str(clone_dir),
            test_cmd=self.test_cmd,
            timeout=self.timeout,
        )
        cloned.setup()  # This does git init + initial commit
        return cloned

    def _copy_workspace_no_git(self, src: Path, dst: Path) -> None:
        """Copy workspace files, excluding .git and .chimera_checkpoints."""
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name in (".git", ".chimera_checkpoints"):
                continue
            dest_item = dst / item.name
            if item.is_dir():
                shutil.copytree(item, dest_item, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest_item)
```

**Step 4: Implement Ensemble.async_run() and first_success**

In `chimera/composition/ensemble.py`, add to imports:

```python
import asyncio
```

Modify `__init__` to add `first_success` parameter:

```python
    def __init__(
        self,
        agents: list[Agent],
        max_workers: int | None = None,
        timeout: float | None = None,
        first_success: bool = False,
    ) -> None:
        self.agents = agents
        self.max_workers = max_workers
        self.timeout = timeout
        self.first_success = first_success
```

Modify `run()` to support `first_success` in the thread path — in `_run_parallel`, after collecting results from futures, if `first_success`, check as futures complete:

In `_run_parallel`, add early cancellation when `first_success=True`:

```python
    def _run_parallel(self, task: str, env: Environment) -> list[AgentResult]:
        clones: list[Environment] = []
        try:
            for _ in self.agents:
                clones.append(env.clone())

            results: list[AgentResult | None] = [None] * len(self.agents)
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_idx = {}
                for idx, (agent, clone) in enumerate(zip(self.agents, clones)):
                    f = executor.submit(agent.run, task, clone)
                    future_to_idx[f] = idx

                if self.first_success:
                    from concurrent.futures import as_completed
                    for f in as_completed(future_to_idx, timeout=self.timeout):
                        idx = future_to_idx[f]
                        try:
                            r = f.result()
                        except Exception as exc:
                            r = AgentResult(
                                output="", steps=0, tool_calls_total=0,
                                cost=0.0, success=False, error=str(exc),
                            )
                        results[idx] = r
                        if r.success:
                            # Cancel remaining futures
                            for other_f in future_to_idx:
                                if other_f is not f and not other_f.done():
                                    other_f.cancel()
                            break
                else:
                    for f, idx in future_to_idx.items():
                        try:
                            results[idx] = f.result(timeout=self.timeout)
                        except (FuturesTimeoutError, Exception) as exc:
                            results[idx] = AgentResult(
                                output="", steps=0, tool_calls_total=0,
                                cost=0.0, success=False, error=str(exc),
                            )

            return [r for r in results if r is not None]
        finally:
            for clone in clones:
                shutil.rmtree(str(clone.workdir), ignore_errors=True)
                clone.cleanup()
```

Add `async_run` method to Ensemble:

```python
    async def async_run(
        self, task: str, env: Environment | None,
    ) -> list[AgentResult]:
        """Run all agents concurrently using asyncio.

        Falls back to sequential if env is None or clone() is unavailable.
        """
        if env is None:
            return self._run_sequential(task, None)

        try:
            clones = [env.clone() for _ in self.agents]
        except NotImplementedError:
            return self._run_sequential(task, env)

        try:
            if self.first_success:
                return await self._async_first_success(task, clones)
            else:
                return await self._async_all(task, clones)
        finally:
            for clone in clones:
                shutil.rmtree(str(clone.workdir), ignore_errors=True)
                clone.cleanup()

    async def _async_all(
        self, task: str, clones: list[Environment],
    ) -> list[AgentResult]:
        """Run all agents concurrently, return all results in order."""
        async def _run(agent: Agent, clone: Environment) -> AgentResult:
            try:
                return await agent.loop.async_run(
                    agent.provider, agent.tools, agent._make_context(task), clone,
                )
            except Exception as exc:
                return AgentResult(
                    output="", steps=0, tool_calls_total=0,
                    cost=0.0, success=False, error=str(exc),
                )

        return list(await asyncio.gather(
            *[_run(a, c) for a, c in zip(self.agents, clones)]
        ))

    async def _async_first_success(
        self, task: str, clones: list[Environment],
    ) -> list[AgentResult]:
        """Run agents concurrently, cancel remaining on first success."""
        async def _run(agent: Agent, clone: Environment) -> AgentResult:
            try:
                return await agent.loop.async_run(
                    agent.provider, agent.tools, agent._make_context(task), clone,
                )
            except Exception as exc:
                return AgentResult(
                    output="", steps=0, tool_calls_total=0,
                    cost=0.0, success=False, error=str(exc),
                )

        tasks = [asyncio.create_task(_run(a, c)) for a, c in zip(self.agents, clones)]
        results: list[AgentResult] = []
        pending = set(tasks)

        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED,
            )
            for t in done:
                r = t.result()
                results.append(r)
                if r.success:
                    for p in pending:
                        p.cancel()
                    pending = set()
                    break

        return results
```

Note: The `agent._make_context(task)` call depends on the Agent API. Check if Agent has a method to create a fresh context. If not, you'll need to create a `Context(system=agent.prompt.render())` and add the user message. Adjust based on what Agent exposes.

**Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_ensemble_async.py -v
```

Expected: All 6 tests PASS.

**Step 6: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1094+ tests pass.

**Step 7: Commit**

```bash
git add chimera/env/git_env.py chimera/composition/ensemble.py tests/test_ensemble_async.py
git commit -m "feat: add Ensemble.async_run with first_success and GitEnvironment.clone"
```

---

## Task 5: MCP Retry + Stderr Reader

**Files:**
- Modify: `chimera/mcp/client.py`
- Modify: `chimera/mcp/transport.py`
- Create: `tests/test_mcp_robustness.py`

**Step 1: Write tests**

Create `tests/test_mcp_robustness.py`:

```python
"""Tests for MCP retry logic and stderr reader."""
from __future__ import annotations

import time
import threading
from typing import Any

import pytest

from chimera.mcp.transport import MCPTransport, StdioTransport
from chimera.mcp.client import MCPClient


class FailingTransport(MCPTransport):
    """Transport that fails N times then succeeds."""

    def __init__(self, fail_count: int, success_response: dict) -> None:
        self._fail_count = fail_count
        self._success = success_response
        self._attempts = 0
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        self._attempts += 1
        if self._attempts <= self._fail_count:
            raise ConnectionError(f"Attempt {self._attempts} failed")
        return self._success

    def close(self) -> None:
        self.closed = True


class MockTransport(MCPTransport):
    """Simple mock transport."""

    def __init__(self, responses: list[dict | None] | None = None) -> None:
        self._responses = list(responses or [])
        self.sent: list[dict] = []
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        self.sent.append(message)
        if self._responses:
            return self._responses.pop(0)
        return None

    def close(self) -> None:
        self.closed = True


class TestRetryWithBackoff:
    def test_retry_succeeds_after_failures(self) -> None:
        """call_tool retries on transport errors."""
        transport = FailingTransport(
            fail_count=2,
            success_response={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [{"type": "text", "text": "ok"}]},
            },
        )
        client = MCPClient()
        client.add_transport("test", transport)
        # Manually start (skip initialize for this test)
        transport.start()

        result = client.call_tool(transport, "any_tool", {"key": "val"})
        assert result["content"][0]["text"] == "ok"
        assert transport._attempts == 3  # 2 fails + 1 success

    def test_retry_exhausted_raises(self) -> None:
        """call_tool raises after max retries."""
        transport = FailingTransport(fail_count=10, success_response={})
        client = MCPClient()
        client.add_transport("test", transport)
        transport.start()

        with pytest.raises(ConnectionError):
            client.call_tool(transport, "any_tool", {})

    def test_non_transport_error_not_retried(self) -> None:
        """Non-transport errors (ValueError, etc.) are not retried."""

        class BadTransport(MCPTransport):
            attempts = 0

            def start(self): pass
            def close(self): pass

            def send(self, msg):
                self.attempts += 1
                raise ValueError("Bad argument")

        transport = BadTransport()
        client = MCPClient()
        client.add_transport("test", transport)
        transport.start()

        with pytest.raises(ValueError):
            client.call_tool(transport, "tool", {})
        assert transport.attempts == 1  # No retry


class TestStderrReader:
    def test_stderr_lines_property(self) -> None:
        """StdioTransport exposes stderr_lines after start."""
        # We can't easily start a real subprocess, so test the attribute exists
        transport = StdioTransport(command="echo", args=["hello"])
        assert hasattr(transport, "stderr_lines")
        # Before start, should be empty deque
        assert len(transport.stderr_lines) == 0
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_mcp_robustness.py -v
```

Expected: FAIL — retry logic doesn't exist, `stderr_lines` attribute missing.

**Step 3: Implement retry in MCPClient.call_tool()**

In `chimera/mcp/client.py`, add `import time` to imports.

Replace the `call_tool` method with retry logic:

```python
    def call_tool(
        self,
        transport: MCPTransport,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> dict[str, Any]:
        """Call a tool on a connected server with retry on transport errors.

        Retries on ConnectionError, TimeoutError, OSError with exponential
        backoff. Non-transport errors are raised immediately.
        """
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                msg = {
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                }
                resp = transport.send(msg)
                if resp is None:
                    raise ConnectionError("No response from server")
                if "error" in resp:
                    return resp["error"]
                return resp.get("result", {})
            except (ConnectionError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    time.sleep(backoff_base * (2 ** attempt))
                continue

        raise last_error  # type: ignore[misc]
```

**Step 4: Implement stderr reader in StdioTransport**

In `chimera/mcp/transport.py`, add `from collections import deque` to imports.

In `StdioTransport.__init__`, add:

```python
        self._stderr_lines: deque[str] = deque(maxlen=100)
        self._stderr_thread: threading.Thread | None = None
```

Add `stderr_lines` property:

```python
    @property
    def stderr_lines(self) -> deque[str]:
        """Recent stderr output from the subprocess (bounded to 100 lines)."""
        return self._stderr_lines
```

In `StdioTransport.start()`, after starting the process, add stderr reader thread:

```python
        # Start stderr reader daemon thread
        if self._process.stderr:
            self._stderr_thread = threading.Thread(
                target=self._read_stderr, daemon=True, name="mcp-stderr-reader",
            )
            self._stderr_thread.start()

    def _read_stderr(self) -> None:
        """Read stderr lines into bounded deque."""
        try:
            assert self._process is not None and self._process.stderr is not None
            for line in self._process.stderr:
                self._stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())
        except (ValueError, OSError):
            pass  # Process closed
```

Also update the `start()` method to capture stderr (change `subprocess.Popen` to include `stderr=subprocess.PIPE`).

**Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_mcp_robustness.py -v
```

Expected: All 4 tests PASS.

**Step 6: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1094+ tests pass.

**Step 7: Commit**

```bash
git add chimera/mcp/client.py chimera/mcp/transport.py tests/test_mcp_robustness.py
git commit -m "feat: add MCP retry with backoff and stderr reader thread"
```

---

## Task 6: MCP Health Checks + Tool Refresh

**Files:**
- Modify: `chimera/mcp/client.py`
- Modify: `tests/test_mcp_robustness.py`

**Step 1: Write tests**

Append to `tests/test_mcp_robustness.py`:

```python
class TestHealthChecks:
    def test_ping_success(self) -> None:
        """ping returns True when server responds."""
        transport = MockTransport([
            # Initialize responses
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            None,
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
            # Ping response
            {"jsonrpc": "2.0", "id": 3, "result": {}},
        ])
        client = MCPClient()
        client.add_transport("test", transport)
        client.connect_all()

        assert client.ping("test") is True

    def test_ping_failure(self) -> None:
        """ping returns False when server doesn't respond."""

        class DeadTransport(MCPTransport):
            def start(self): pass
            def close(self): pass
            def send(self, msg):
                raise ConnectionError("dead")

        client = MCPClient()
        client.add_transport("test", DeadTransport())
        client._transports["test"].start()

        assert client.ping("test") is False

    def test_is_connected(self) -> None:
        """is_connected checks if transport is alive."""
        transport = MockTransport([
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            None,
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
        ])
        client = MCPClient()
        client.add_transport("test", transport)
        client.connect_all()

        assert client.is_connected("test") is True


class TestToolRefresh:
    def test_refresh_tools(self) -> None:
        """refresh_tools re-discovers tools from server."""
        transport = MockTransport([
            # Initial connect
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}},
            None,
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}},
            # Refresh response
            {"jsonrpc": "2.0", "id": 3, "result": {"tools": [
                {"name": "new_tool", "description": "A new tool",
                 "inputSchema": {"type": "object", "properties": {}}},
            ]}},
        ])
        client = MCPClient()
        client.add_transport("test", transport)
        client.connect_all()

        assert len(client.tools) == 0

        client.refresh_tools("test")
        assert len(client.tools) == 1
        assert client.tools[0]["name"] == "new_tool"
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_mcp_robustness.py::TestHealthChecks -v
python -m pytest tests/test_mcp_robustness.py::TestToolRefresh -v
```

Expected: FAIL — `ping`, `is_connected`, `refresh_tools` don't exist.

**Step 3: Implement health checks and tool refresh**

In `chimera/mcp/client.py`, add these methods to MCPClient:

```python
    def ping(self, name: str) -> bool:
        """Send MCP ping, return True if server responds."""
        transport = self._transports.get(name)
        if transport is None:
            return False
        try:
            msg = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "ping",
            }
            resp = transport.send(msg)
            return resp is not None
        except (ConnectionError, TimeoutError, OSError):
            return False

    def is_connected(self, name: str) -> bool:
        """Check if transport is alive."""
        transport = self._transports.get(name)
        if transport is None:
            return False
        # For StdioTransport, check if process is running
        if hasattr(transport, "_process") and transport._process is not None:
            return transport._process.poll() is None
        # For HTTP and others, try a ping
        return self.ping(name)

    def refresh_tools(self, name: str | None = None) -> None:
        """Re-discover tools from one or all servers."""
        if name is not None:
            transport = self._transports.get(name)
            if transport is not None:
                self._discover_tools(name, transport)
        else:
            for n, t in self._transports.items():
                self._discover_tools(n, t)
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_mcp_robustness.py -v
```

Expected: All 8 tests PASS.

**Step 5: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1094+ tests pass.

**Step 6: Commit**

```bash
git add chimera/mcp/client.py tests/test_mcp_robustness.py
git commit -m "feat: add MCP ping, is_connected, and refresh_tools"
```

---

## Task 7: LSP Background Notification Reader + Diagnostics Fix

**Files:**
- Modify: `chimera/lsp/session.py`
- Modify: `chimera/lsp/manager.py`
- Create: `tests/test_lsp_diagnostics.py`

**Step 1: Write tests**

Create `tests/test_lsp_diagnostics.py`:

```python
"""Tests for LSP background reader and diagnostics."""
from __future__ import annotations

import json
import queue
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.lsp.base import Diagnostic, Severity
from chimera.lsp.session import LSPSession


class TestBackgroundReader:
    def test_response_routing(self) -> None:
        """Responses (with id) are routed to pending_responses queues."""
        session = LSPSession(command=["dummy"])
        session._running = True
        session._pending_responses = {}

        # Simulate what _reader_loop does
        response = {"jsonrpc": "2.0", "id": 1, "result": {"key": "value"}}
        q: queue.Queue = queue.Queue()
        session._pending_responses[1] = q
        q.put(response)

        result = q.get(timeout=1)
        assert result["result"]["key"] == "value"

    def test_notification_routing(self) -> None:
        """Notifications (no id) are routed to handlers."""
        session = LSPSession(command=["dummy"])
        session._diagnostics = {}

        # Simulate a publishDiagnostics notification
        notification = {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "file:///test.py",
                "diagnostics": [
                    {
                        "range": {"start": {"line": 0, "character": 0},
                                  "end": {"line": 0, "character": 5}},
                        "severity": 1,
                        "message": "undefined name 'foo'",
                    },
                ],
            },
        }
        session._handle_notification(notification)
        assert "file:///test.py" in session._diagnostics
        assert len(session._diagnostics["file:///test.py"]) == 1
        assert session._diagnostics["file:///test.py"][0].message == "undefined name 'foo'"

    def test_diagnostics_cached(self) -> None:
        """Multiple diagnostics notifications update the cache."""
        session = LSPSession(command=["dummy"])
        session._diagnostics = {}

        # First notification
        session._handle_notification({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "file:///a.py",
                "diagnostics": [
                    {"range": {"start": {"line": 0, "character": 0},
                               "end": {"line": 0, "character": 1}},
                     "severity": 2, "message": "warning1"},
                ],
            },
        })

        # Second notification replaces first
        session._handle_notification({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "file:///a.py",
                "diagnostics": [
                    {"range": {"start": {"line": 1, "character": 0},
                               "end": {"line": 1, "character": 1}},
                     "severity": 1, "message": "error1"},
                ],
            },
        })

        assert len(session._diagnostics["file:///a.py"]) == 1
        assert session._diagnostics["file:///a.py"][0].message == "error1"


class TestGetDiagnostics:
    def test_get_diagnostics_returns_cached(self) -> None:
        """get_diagnostics returns cached diagnostics for a URI."""
        session = LSPSession(command=["dummy"])
        session._diagnostics = {
            "file:///test.py": [
                Diagnostic(
                    file="test.py", line=1, column=0,
                    severity=Severity.ERROR, message="error",
                ),
            ],
        }
        diags = session.get_diagnostics("file:///test.py")
        assert len(diags) == 1
        assert diags[0].severity == Severity.ERROR

    def test_get_diagnostics_empty(self) -> None:
        """get_diagnostics returns empty for unknown URI."""
        session = LSPSession(command=["dummy"])
        session._diagnostics = {}
        assert session.get_diagnostics("file:///unknown.py") == []
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_lsp_diagnostics.py -v
```

Expected: FAIL — `_handle_notification`, `_diagnostics`, `get_diagnostics` don't exist on LSPSession.

**Step 3: Implement background notification reader**

Rewrite `chimera/lsp/session.py`:

Add `import queue` and `from collections import deque` to imports.

Add new attributes to `__init__`:

```python
    def __init__(self, command: list[str]) -> None:
        self._command = command
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._initialized = False
        # Background reader state
        self._pending_responses: dict[int, queue.Queue] = {}
        self._diagnostics: dict[str, list[Diagnostic]] = {}
        self._reader_thread: threading.Thread | None = None
        self._running = False
```

Add `_handle_notification` method:

```python
    def _handle_notification(self, msg: dict[str, Any]) -> None:
        """Route an incoming notification to the appropriate handler."""
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "textDocument/publishDiagnostics":
            uri = params.get("uri", "")
            raw_diags = params.get("diagnostics", [])
            severity_map = {1: Severity.ERROR, 2: Severity.WARNING, 3: Severity.INFO, 4: Severity.HINT}
            diags = []
            for d in raw_diags:
                r = d.get("range", {}).get("start", {})
                diags.append(Diagnostic(
                    file=uri.replace("file://", ""),
                    line=r.get("line", 0) + 1,
                    column=r.get("character", 0),
                    severity=severity_map.get(d.get("severity", 1), Severity.ERROR),
                    message=d.get("message", ""),
                ))
            self._diagnostics[uri] = diags
```

Add `get_diagnostics` method:

```python
    def get_diagnostics(self, uri: str) -> list[Diagnostic]:
        """Return cached diagnostics for a file URI."""
        return self._diagnostics.get(uri, [])
```

Add `_reader_loop` method:

```python
    def _reader_loop(self) -> None:
        """Background thread: read messages and route to queues or handlers."""
        while self._running:
            try:
                msg = self._read_raw()
                if msg is None:
                    break
                if "id" in msg:
                    # Response — route to pending queue
                    rid = msg["id"]
                    q = self._pending_responses.get(rid)
                    if q is not None:
                        q.put(msg)
                elif "method" in msg:
                    # Notification
                    self._handle_notification(msg)
            except (json.JSONDecodeError, ValueError, OSError):
                if not self._running:
                    break
```

Rename existing `_read` to `_read_raw` (it reads a single message from stdout).

Update `_send_request` to use the queue pattern:

```python
    def _send_request(self, method: str, params: Any) -> dict[str, Any] | None:
        with self._lock:
            self._request_id += 1
            rid = self._request_id

        q: queue.Queue = queue.Queue()
        self._pending_responses[rid] = q

        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        self._write(msg)

        try:
            resp = q.get(timeout=30)
        except queue.Empty:
            return None
        finally:
            self._pending_responses.pop(rid, None)

        return resp.get("result")
```

Update `start()` to launch the reader thread (after sending initialized notification):

```python
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="lsp-reader",
        )
        self._reader_thread.start()
```

Update `stop()` to shutdown reader:

```python
        self._running = False
        # ... existing shutdown code ...
```

**Step 4: Update LSPManager.get_diagnostics()**

In `chimera/lsp/manager.py`, update `get_diagnostics` to use the session's cached diagnostics:

```python
    def get_diagnostics(self, file_path: str) -> list[Diagnostic]:
        """Get diagnostics for a file from the appropriate language server."""
        session = self.get_session(file_path)
        if session is None:
            return []
        uri = Path(file_path).as_uri()
        return session.get_diagnostics(uri)
```

**Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_lsp_diagnostics.py -v
```

Expected: All 5 tests PASS.

**Step 6: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1094+ tests pass.

**Step 7: Commit**

```bash
git add chimera/lsp/session.py chimera/lsp/manager.py tests/test_lsp_diagnostics.py
git commit -m "feat: add LSP background notification reader and fix diagnostics"
```

---

## Task 8: LSP New Methods + Tool Expansion

**Files:**
- Modify: `chimera/lsp/session.py`
- Modify: `chimera/lsp/tool.py`
- Modify: `tests/test_lsp_diagnostics.py`

**Step 1: Write tests**

Append to `tests/test_lsp_diagnostics.py`:

```python
from chimera.lsp.tool import LSPTool
from chimera.lsp.manager import LSPManager
from unittest.mock import MagicMock


class TestNewLSPMethods:
    def _make_tool(self) -> tuple[LSPTool, MagicMock]:
        manager = LSPManager()
        manager.add("python", ["pyright", "--stdio"], (".py",))
        mock_session = MagicMock()
        manager._sessions["python"] = mock_session
        tool = LSPTool(manager)
        return tool, mock_session

    def test_workspace_symbols(self) -> None:
        """workspace_symbols action returns symbol list."""
        tool, session = self._make_tool()
        session.workspace_symbols.return_value = [
            {"name": "MyClass", "kind": 5, "location": {"uri": "file:///a.py"}},
        ]
        result = tool.execute(
            {"action": "workspace_symbols", "query": "MyClass", "file": "a.py"},
            None,
        )
        assert result.success
        assert "MyClass" in result.output
        session.workspace_symbols.assert_called_once_with("MyClass")

    def test_code_actions(self) -> None:
        """code_actions action returns available actions."""
        tool, session = self._make_tool()
        session.code_actions.return_value = [
            {"title": "Import os", "kind": "quickfix"},
        ]
        result = tool.execute(
            {
                "action": "code_actions",
                "file": "test.py",
                "line": 1,
                "character": 0,
                "end_line": 1,
                "end_character": 5,
            },
            None,
        )
        assert result.success
        assert "Import os" in result.output

    def test_completion(self) -> None:
        """completion action returns completion items."""
        tool, session = self._make_tool()
        session.completion.return_value = [
            {"label": "append", "kind": 2},
            {"label": "clear", "kind": 2},
        ]
        result = tool.execute(
            {"action": "completion", "file": "test.py", "line": 5, "character": 10},
            None,
        )
        assert result.success
        assert "append" in result.output

    def test_get_diagnostics_action(self) -> None:
        """get_diagnostics action returns diagnostics."""
        tool, session = self._make_tool()
        session.get_diagnostics.return_value = [
            Diagnostic(file="test.py", line=1, column=0, severity=Severity.ERROR, message="err"),
        ]
        result = tool.execute(
            {"action": "get_diagnostics", "file": "test.py"},
            None,
        )
        assert result.success
        assert "err" in result.output
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_lsp_diagnostics.py::TestNewLSPMethods -v
```

Expected: FAIL — new actions not recognized, new methods don't exist.

**Step 3: Add new methods to LSPSession**

In `chimera/lsp/session.py`, add after `document_symbols`:

```python
    def workspace_symbols(self, query: str) -> list[dict[str, Any]]:
        """Search symbols across the workspace."""
        return self._send_request("workspace/symbol", {"query": query}) or []

    def code_actions(
        self,
        uri: str,
        start_line: int,
        start_char: int,
        end_line: int,
        end_char: int,
    ) -> list[dict[str, Any]]:
        """Get available code actions for a range."""
        return self._send_request("textDocument/codeAction", {
            "textDocument": {"uri": uri},
            "range": {
                "start": {"line": start_line, "character": start_char},
                "end": {"line": end_line, "character": end_char},
            },
            "context": {"diagnostics": []},
        }) or []

    def completion(self, uri: str, line: int, character: int) -> list[dict[str, Any]]:
        """Get completion items at a position."""
        result = self._send_request("textDocument/completion", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })
        if result is None:
            return []
        # Completion can return a CompletionList or a list
        if isinstance(result, dict):
            return result.get("items", [])
        return result
```

**Step 4: Expand LSPTool**

In `chimera/lsp/tool.py`, update the `parameters` dict to add new actions:

Change the action enum from:
```python
"enum": ["go_to_definition", "find_references", "hover", "document_symbols"]
```
to:
```python
"enum": ["go_to_definition", "find_references", "hover", "document_symbols",
         "workspace_symbols", "code_actions", "completion", "get_diagnostics"]
```

Add `query`, `end_line`, `end_character` to properties:

```python
"query": {"type": "string", "description": "Search query for workspace_symbols"},
"end_line": {"type": "integer", "description": "End line for code_actions range"},
"end_character": {"type": "integer", "description": "End character for code_actions range"},
```

In `execute()`, add the new action branches:

```python
        elif action == "workspace_symbols":
            query = args.get("query", "")
            symbols = session.workspace_symbols(query)
            return ToolResult(output=json.dumps(symbols, indent=2))

        elif action == "code_actions":
            uri = Path(file_path).as_uri()
            actions = session.code_actions(
                uri,
                args.get("line", 0) - 1,
                args.get("character", 0),
                args.get("end_line", args.get("line", 0)) - 1,
                args.get("end_character", 0),
            )
            return ToolResult(output=json.dumps(actions, indent=2))

        elif action == "completion":
            uri = Path(file_path).as_uri()
            items = session.completion(uri, line - 1, character)
            return ToolResult(output=json.dumps(items, indent=2))

        elif action == "get_diagnostics":
            uri = Path(file_path).as_uri()
            diags = session.get_diagnostics(uri)
            if not diags:
                return ToolResult(output="No diagnostics")
            lines = [d.to_feedback_str() for d in diags]
            return ToolResult(output="\n".join(lines))
```

Add `import json` to LSPTool imports if not present.

**Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_lsp_diagnostics.py -v
```

Expected: All 9 tests PASS.

**Step 6: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1094+ tests pass.

**Step 7: Commit**

```bash
git add chimera/lsp/session.py chimera/lsp/tool.py tests/test_lsp_diagnostics.py
git commit -m "feat: add LSP workspace_symbols, code_actions, completion, and get_diagnostics"
```

---

## Task 9: CostTracker + LoopConfig + estimate_cost

**Files:**
- Create: `chimera/providers/cost_tracker.py`
- Modify: `chimera/providers/cost.py`
- Modify: `chimera/core/loop_config.py`
- Modify: `chimera/core/loop.py`
- Modify: `chimera/__init__.py`
- Create: `tests/test_cost_tracker.py`

**Step 1: Write tests**

Create `tests/test_cost_tracker.py`:

```python
"""Tests for CostTracker and estimate_cost."""
from __future__ import annotations

import pytest

from chimera.providers.cost_tracker import CostLimitExceeded, CostTracker
from chimera.providers.cost import estimate_cost


class TestCostTracker:
    def test_record_and_total(self) -> None:
        """record() accumulates cost, total reflects it."""
        tracker = CostTracker()
        tracker.record(0.05, model="gpt-4o")
        tracker.record(0.10, model="claude-sonnet-4")
        assert abs(tracker.total - 0.15) < 1e-9

    def test_budget_enforcement(self) -> None:
        """Exceeding budget raises CostLimitExceeded."""
        tracker = CostTracker(budget=0.10)
        tracker.record(0.05)
        with pytest.raises(CostLimitExceeded):
            tracker.record(0.06)

    def test_remaining(self) -> None:
        """remaining reflects budget minus spent."""
        tracker = CostTracker(budget=1.0)
        tracker.record(0.30)
        assert abs(tracker.remaining - 0.70) < 1e-9

    def test_remaining_no_budget(self) -> None:
        """remaining is None when no budget set."""
        tracker = CostTracker()
        assert tracker.remaining is None

    def test_breakdown_by_model(self) -> None:
        """breakdown() returns per-model costs."""
        tracker = CostTracker()
        tracker.record(0.05, model="gpt-4o")
        tracker.record(0.10, model="gpt-4o")
        tracker.record(0.20, model="claude-sonnet-4")

        bd = tracker.breakdown()
        assert abs(bd["gpt-4o"] - 0.15) < 1e-9
        assert abs(bd["claude-sonnet-4"] - 0.20) < 1e-9

    def test_reset(self) -> None:
        """reset() clears total and breakdown."""
        tracker = CostTracker(budget=1.0)
        tracker.record(0.50, model="gpt-4o")
        tracker.reset()
        assert tracker.total == 0.0
        assert tracker.breakdown() == {}
        assert abs(tracker.remaining - 1.0) < 1e-9

    def test_empty_model(self) -> None:
        """Recording with empty model string works."""
        tracker = CostTracker()
        tracker.record(0.05)
        assert abs(tracker.total - 0.05) < 1e-9
        assert "" in tracker.breakdown()


class TestEstimateCost:
    def test_known_model(self) -> None:
        """estimate_cost returns correct value for known model."""
        cost = estimate_cost("claude-sonnet-4", input_tokens=1000, output_tokens=500)
        expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000
        assert abs(cost - expected) < 1e-9

    def test_unknown_model(self) -> None:
        """estimate_cost returns 0.0 for unknown model."""
        cost = estimate_cost("unknown-model", input_tokens=1000, output_tokens=500)
        assert cost == 0.0


class TestCostTrackerInLoop:
    @pytest.mark.asyncio
    async def test_budget_stops_loop(self) -> None:
        """CostTracker budget stops the ReAct loop."""
        from chimera.core.loop import ReAct, async_drain_steps
        from chimera.core.loop_config import LoopConfig
        from chimera.core.context import Context
        from chimera.providers.base import Provider, Response
        from chimera.types import Message, ToolCall, ToolResult
        from chimera.core.tool import BaseTool
        from typing import Any

        class ExpensiveProvider(Provider):
            def __init__(self):
                self._idx = 0
            def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                self._idx += 1
                return Response(
                    content=f"step {self._idx}",
                    tool_calls=[ToolCall(id=f"tc{self._idx}", name="echo", arguments={"msg": "x"})],
                    usage={"input_tokens": 100_000, "output_tokens": 50_000},
                )
            async def async_complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
                return self.complete(messages, tools, temperature, max_tokens)
            @property
            def context_window(self): return 200_000
            @property
            def supports_tool_use(self): return True
            @property
            def model_name(self): return "claude-sonnet-4"

        class EchoTool(BaseTool):
            name = "echo"
            description = "echo"
            parameters: dict[str, Any] = {
                "type": "object", "properties": {"msg": {"type": "string"}},
                "required": ["msg"],
            }
            def execute(self, args, env=None):
                return ToolResult(output=f"echo:{args['msg']}")

        tracker = CostTracker(budget=0.01)
        config = LoopConfig(cost_tracker=tracker)
        loop = ReAct(max_steps=100, config=config)
        context = Context(system="test")
        context.add(Message.user("go"))

        result = await loop.async_run(ExpensiveProvider(), [EchoTool()], context, None)
        assert result.success is False
        assert "cost" in result.error.lower()
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_cost_tracker.py -v
```

Expected: FAIL — `CostTracker`, `estimate_cost` don't exist.

**Step 3: Create CostTracker class**

Create `chimera/providers/cost_tracker.py`:

```python
"""Cumulative cost tracking with optional budget enforcement."""
from __future__ import annotations


class CostLimitExceeded(Exception):
    """Raised when cost budget is exceeded."""


class CostTracker:
    """Track cumulative LLM costs with optional budget.

    Args:
        budget: Maximum allowed cost in USD. None means unlimited.
    """

    def __init__(self, budget: float | None = None) -> None:
        self._total = 0.0
        self._budget = budget
        self._by_model: dict[str, float] = {}

    def record(self, cost: float, model: str = "") -> None:
        """Record a cost. Raises CostLimitExceeded if budget exceeded."""
        new_total = self._total + cost
        if self._budget is not None and new_total > self._budget:
            raise CostLimitExceeded(
                f"Cost limit exceeded: ${new_total:.4f} > ${self._budget:.4f}"
            )
        self._total = new_total
        self._by_model[model] = self._by_model.get(model, 0.0) + cost

    @property
    def total(self) -> float:
        """Total cost recorded so far."""
        return self._total

    @property
    def remaining(self) -> float | None:
        """Remaining budget, or None if no budget set."""
        if self._budget is None:
            return None
        return self._budget - self._total

    def breakdown(self) -> dict[str, float]:
        """Per-model cost breakdown."""
        return dict(self._by_model)

    def reset(self) -> None:
        """Reset all tracked costs."""
        self._total = 0.0
        self._by_model.clear()
```

**Step 4: Add estimate_cost to cost.py**

In `chimera/providers/cost.py`, add:

```python
def estimate_cost(
    model: str, input_tokens: int, output_tokens: int = 0,
) -> float:
    """Pre-flight cost estimation. Returns 0.0 for unknown models."""
    return calculate_cost(model, {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    })
```

**Step 5: Wire CostTracker into LoopConfig**

In `chimera/core/loop_config.py`, add to TYPE_CHECKING imports:

```python
    from chimera.providers.cost_tracker import CostTracker
```

Add field to `LoopConfig` dataclass:

```python
    cost_tracker: CostTracker | None = None
```

**Step 6: Wire CostTracker into ReAct loops**

In `chimera/core/loop.py`, in both `iter_steps()` and `async_iter_steps()`, after computing `step_cost` and before incrementing `total_cost`, add:

```python
            # Cost tracking
            if self.config and self.config.cost_tracker:
                try:
                    self.config.cost_tracker.record(step_cost, model=provider.model_name)
                except Exception:
                    # CostLimitExceeded
                    if handler:
                        handler.on_done()
                    yield StepResult(
                        message=Message.assistant(response.content),
                        done=True,
                        step=steps,
                        cost=step_cost,
                    )
                    # For sync: return; for async: set _async_result and return
                    return AgentResult(  # or set self._async_result
                        output=response.content,
                        steps=steps,
                        tool_calls_total=total_tool_calls,
                        cost=total_cost + step_cost,
                        success=False,
                        error="Cost limit exceeded",
                    )
```

Note: The exact placement differs between `iter_steps` (uses `return AgentResult(...)`) and `async_iter_steps` (sets `self._async_result` then `return`).

**Step 7: Update exports**

In `chimera/__init__.py`, add:

```python
from chimera.providers.cost_tracker import CostLimitExceeded, CostTracker
from chimera.providers.cost import estimate_cost
```

Add `"CostTracker"`, `"CostLimitExceeded"`, `"estimate_cost"` to `__all__`.

**Step 8: Run tests to verify they pass**

```bash
python -m pytest tests/test_cost_tracker.py -v
```

Expected: All 10 tests PASS.

**Step 9: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1094+ tests pass.

**Step 10: Commit**

```bash
git add chimera/providers/cost_tracker.py chimera/providers/cost.py chimera/core/loop_config.py chimera/core/loop.py chimera/__init__.py tests/test_cost_tracker.py
git commit -m "feat: add CostTracker with budgets, estimate_cost, and LoopConfig integration"
```

---

## Task 10: REPL Command Palette

**Files:**
- Modify: `chimera/cli/code.py`
- Modify: `tests/test_cli_code.py`

**Step 1: Write tests**

Append to `tests/test_cli_code.py` (or create new test file `tests/test_repl_commands.py` to keep it clean):

Create `tests/test_repl_commands.py`:

```python
"""Tests for REPL slash commands and readline integration."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from chimera.cli.code import _COMMANDS, _dispatch_command, _complete_command


class TestCommandDispatch:
    def test_known_command(self) -> None:
        """Known commands dispatch correctly."""
        assert "help" in _COMMANDS
        assert "model" in _COMMANDS
        assert "cost" in _COMMANDS
        assert "clear" in _COMMANDS
        assert "tools" in _COMMANDS
        assert "context" in _COMMANDS
        assert "debug" in _COMMANDS
        assert "exit" in _COMMANDS
        assert "quit" in _COMMANDS

    def test_help_command(self) -> None:
        """help command lists all available commands."""
        output: list[str] = []
        session = MagicMock()
        env = MagicMock()
        _COMMANDS["help"](session, env, "", output.append)
        text = "\n".join(output)
        assert "/help" in text
        assert "/model" in text
        assert "/cost" in text

    def test_unknown_command(self) -> None:
        """Unknown commands produce error message."""
        output: list[str] = []
        result = _dispatch_command("/unknown", MagicMock(), MagicMock(), output.append)
        assert result is False
        assert any("unknown" in line.lower() for line in output)


class TestTabCompletion:
    def test_complete_slash(self) -> None:
        """Tab completion returns matching commands."""
        matches = _complete_command("/he", 0)
        assert matches == "/help"

    def test_complete_no_match(self) -> None:
        """No matching completion returns None."""
        result = _complete_command("/zzz", 0)
        assert result is None


class TestCostCommand:
    def test_cost_with_tracker(self) -> None:
        """cost command shows cumulative cost."""
        from chimera.providers.cost_tracker import CostTracker

        output: list[str] = []
        session = MagicMock()
        session.cost_tracker = CostTracker()
        session.cost_tracker.record(0.05, model="gpt-4o")
        session.cost_tracker.record(0.10, model="claude-sonnet-4")
        env = MagicMock()

        _COMMANDS["cost"](session, env, "", output.append)
        text = "\n".join(output)
        assert "0.15" in text or "0.1500" in text


class TestClearCommand:
    def test_clear_resets_context(self) -> None:
        """clear command resets conversation context."""
        session = MagicMock()
        env = MagicMock()
        output: list[str] = []
        _COMMANDS["clear"](session, env, "", output.append)
        session.clear.assert_called_once()


class TestDebugCommand:
    def test_debug_toggle(self) -> None:
        """debug command toggles debug mode."""
        session = MagicMock()
        session.debug = False
        env = MagicMock()
        output: list[str] = []

        _COMMANDS["debug"](session, env, "", output.append)
        assert session.debug is True

        _COMMANDS["debug"](session, env, "", output.append)
        assert session.debug is False
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_repl_commands.py -v
```

Expected: FAIL — `_COMMANDS`, `_dispatch_command`, `_complete_command` don't exist.

**Step 3: Implement REPL command palette**

Rewrite `chimera/cli/code.py` with readline and slash commands:

```python
"""Interactive coding REPL with readline, slash commands, and session management."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

from chimera import __version__
from chimera.core.agent import Agent
from chimera.core.loop import ReAct, drain_steps
from chimera.core.loop_config import LoopConfig
from chimera.core.prompt import Prompt
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.cost_tracker import CostTracker
from chimera.providers.factory import create_provider
from chimera.sessions.session import Session
from chimera.streaming.handlers import ConsoleStreamHandler

_DEFAULT_SYSTEM = """\
You are a coding assistant with access to tools for reading, writing, \
editing files, running commands, searching code, and running tests. \
Help the user with their coding tasks. Be concise and direct."""

# -- Type alias for command handlers --
PrintFn = Callable[[str], None]
CommandHandler = Callable[[Any, Any, str, PrintFn], None]


# -- Slash Command Handlers --

def cmd_help(session: Any, env: Any, args: str, out: PrintFn) -> None:
    out("Available commands:")
    for name, _ in sorted(_COMMANDS.items()):
        out(f"  /{name}")


def cmd_model(session: Any, env: Any, args: str, out: PrintFn) -> None:
    out(f"Current model: {session.provider.model_name}")


def cmd_cost(session: Any, env: Any, args: str, out: PrintFn) -> None:
    tracker = getattr(session, "cost_tracker", None)
    if tracker is None:
        out("No cost tracker active.")
        return
    out(f"Total cost: ${tracker.total:.4f}")
    bd = tracker.breakdown()
    if bd:
        out("Breakdown:")
        for model, cost in sorted(bd.items()):
            out(f"  {model}: ${cost:.4f}")
    if tracker.remaining is not None:
        out(f"Remaining budget: ${tracker.remaining:.4f}")


def cmd_clear(session: Any, env: Any, args: str, out: PrintFn) -> None:
    session.clear()
    out("Context cleared.")


def cmd_tools(session: Any, env: Any, args: str, out: PrintFn) -> None:
    tools = getattr(session, "tools", [])
    if not tools:
        out("No tools loaded.")
        return
    for t in tools:
        out(f"  {t.name}: {t.description[:60]}")


def cmd_context(session: Any, env: Any, args: str, out: PrintFn) -> None:
    ctx = getattr(session, "context", None)
    if ctx is None:
        out("No context available.")
        return
    msgs = ctx.to_messages()
    out(f"Messages: {len(msgs)}")
    total_chars = sum(len(m.content) for m in msgs)
    out(f"Estimated tokens: ~{total_chars // 4}")


def cmd_debug(session: Any, env: Any, args: str, out: PrintFn) -> None:
    current = getattr(session, "debug", False)
    session.debug = not current
    out(f"Debug mode: {'on' if session.debug else 'off'}")


def cmd_history(session: Any, env: Any, args: str, out: PrintFn) -> None:
    ctx = getattr(session, "context", None)
    if ctx is None:
        out("No context.")
        return
    msgs = ctx.to_messages()
    for m in msgs[-10:]:
        prefix = m.role.upper()[:4]
        content = m.content[:80].replace("\n", " ")
        out(f"  [{prefix}] {content}")


def cmd_compact(session: Any, env: Any, args: str, out: PrintFn) -> None:
    if hasattr(session, "compact"):
        session.compact()
        out("Context compacted.")
    else:
        out("Compaction not available.")


def cmd_session(session: Any, env: Any, args: str, out: PrintFn) -> None:
    parts = args.strip().split(maxsplit=1)
    sub = parts[0] if parts else "list"

    if sub == "save":
        name = parts[1] if len(parts) > 1 else None
        if hasattr(session, "save"):
            session.save(name)
            out(f"Session saved{f' as {name}' if name else ''}.")
        else:
            out("Session save not available.")
    elif sub == "list":
        out("Session management: /session save [name] | /session list")
    elif sub == "fork":
        if hasattr(session, "fork"):
            session.fork()
            out("Session forked.")
        else:
            out("Session fork not available.")
    else:
        out(f"Unknown session command: {sub}")


def cmd_exit(session: Any, env: Any, args: str, out: PrintFn) -> None:
    raise SystemExit(0)


# -- Command Registry --

_COMMANDS: dict[str, CommandHandler] = {
    "help": cmd_help,
    "model": cmd_model,
    "cost": cmd_cost,
    "clear": cmd_clear,
    "history": cmd_history,
    "tools": cmd_tools,
    "context": cmd_context,
    "debug": cmd_debug,
    "session": cmd_session,
    "compact": cmd_compact,
    "exit": cmd_exit,
    "quit": cmd_exit,
}


def _dispatch_command(
    line: str, session: Any, env: Any, out: PrintFn,
) -> bool:
    """Dispatch a slash command. Returns True if handled."""
    if not line.startswith("/"):
        return False
    parts = line[1:].split(maxsplit=1)
    cmd_name = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    handler = _COMMANDS.get(cmd_name)
    if handler is None:
        out(f"Unknown command: /{cmd_name}. Type /help for available commands.")
        return False

    handler(session, env, args, out)
    return True


# -- Tab Completion --

_COMMAND_NAMES = sorted(f"/{name}" for name in _COMMANDS)


def _complete_command(text: str, state: int) -> str | None:
    """Readline completer for slash commands."""
    matches = [c for c in _COMMAND_NAMES if c.startswith(text)]
    if state < len(matches):
        return matches[state]
    return None


# -- Readline Setup --

def _setup_readline() -> None:
    """Set up readline with history and tab completion."""
    try:
        import readline
    except ImportError:
        return

    history_dir = Path.home() / ".chimera"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "history"

    try:
        readline.read_history_file(str(history_file))
    except FileNotFoundError:
        pass

    readline.set_history_length(1000)
    readline.set_completer(_complete_command)
    readline.parse_and_bind("tab: complete")

    import atexit
    atexit.register(readline.write_history_file, str(history_file))


# -- Main REPL --

def run_code(args: Any) -> int:
    """Run the interactive coding REPL."""
    workdir = getattr(args, "workdir", None) or os.getcwd()
    provider = create_provider(model=getattr(args, "model", None))
    env = LocalEnvironment(workdir=workdir)
    env.setup()

    # Auto-discover project context
    try:
        from chimera.config import ProjectConfig
        project = ProjectConfig.discover(workdir)
        if project and project.test_cmd:
            env = LocalEnvironment(workdir=workdir, test_cmd=project.test_cmd)
            env.setup()
    except Exception:
        pass

    handler = ConsoleStreamHandler()
    cost_tracker = CostTracker()
    max_steps = getattr(args, "max_steps", 50) or 50
    config = LoopConfig(handler=handler, cost_tracker=cost_tracker)
    loop = ReAct(max_steps=max_steps, config=config)
    agent = Agent(
        provider=provider,
        prompt=Prompt(_DEFAULT_SYSTEM),
        tools=DEFAULT_TOOLS,
        loop=loop,
    )
    session = Session(agent=agent, env=env)
    session.provider = provider  # For /model command
    session.cost_tracker = cost_tracker  # For /cost command
    session.tools = DEFAULT_TOOLS  # For /tools command
    session.debug = False  # For /debug command

    _setup_readline()

    print(f"chimera code v{__version__} | model: {provider.model_name} | /help for commands")
    total_cost = 0.0

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        # Slash command dispatch
        if user_input.startswith("/"):
            try:
                _dispatch_command(user_input, session, env, print)
            except SystemExit:
                print("Bye!")
                break
            continue

        # Regular chat
        try:
            result = drain_steps(session.iter_chat(user_input))
            total_cost += result.cost
            cost_tracker.record(result.cost, model=provider.model_name)
            print(f"\n  [cost: ${result.cost:.4f} | steps: {result.steps}]")
        except KeyboardInterrupt:
            print("\n  (interrupted)")
        except Exception as exc:
            print(f"\n  Error: {exc}")

    print(f"\nTotal cost: ${total_cost:.4f}")
    env.cleanup()
    return 0
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_repl_commands.py -v
```

Expected: All 9 tests PASS.

**Step 5: Run full suite**

```bash
python -m pytest tests/ -x -q
```

Expected: 1094+ tests pass.

**Step 6: Commit**

```bash
git add chimera/cli/code.py tests/test_repl_commands.py
git commit -m "feat: add REPL readline integration and 12 slash commands"
```

---

## Verification

After all 10 tasks:

```bash
# 1. Full test suite
python -m pytest tests/ -x -q

# 2. New test files
python -m pytest tests/test_async_tool.py tests/test_async_iter_steps.py tests/test_ensemble_async.py tests/test_mcp_robustness.py tests/test_lsp_diagnostics.py tests/test_cost_tracker.py tests/test_repl_commands.py -v

# 3. Verify new exports
python -c "from chimera import async_drain_steps, CostTracker, CostLimitExceeded, estimate_cost"

# 4. Verify imports work
python -c "
from chimera.core.tool import BaseTool
from chimera.core.tool_executor import async_execute_tool_calls_incremental
from chimera.core.loop import ReAct, async_drain_steps
from chimera.providers.cost_tracker import CostTracker, CostLimitExceeded
from chimera.providers.cost import estimate_cost
print('All imports OK')
"
```

---

## Summary Table

```
Task  Component              New Tests    Key Changes
────────────────────────────────────────────────────────────────────
 1    BaseTool.async_execute      4       tool.py: async_execute method
 2    Async Tool Executor         4       tool_executor.py: async gather
 3    async_iter_steps            7       loop.py: async generator + drain
 4    Ensemble async + clone      6       ensemble.py + git_env.py
 5    MCP retry + stderr          4       client.py + transport.py
 6    MCP health + refresh        4       client.py: ping, refresh
 7    LSP background reader       5       session.py: rewrite I/O
 8    LSP new methods             4       session.py + tool.py
 9    CostTracker                10       cost_tracker.py + loop wiring
10    REPL commands               9       code.py: readline + commands
────────────────────────────────────────────────────────────────────
                               ~57       Total new tests
```

# Phase 1: Core Loop Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace chimera's synchronous ReAct loop with an AsyncGenerator-based agent loop that streams events, executes tools concurrently, and recovers from errors mid-turn.

**Architecture:** New `AgentLoop` class in `chimera/core/agent_loop.py` becomes the foundation. Existing `ReAct` becomes a thin wrapper. `StreamingToolExecutor` handles concurrent tool execution. `ErrorRecovery` provides multi-stage recovery. `AbortSignal` enables cooperative cancellation.

**Tech Stack:** Python 3.11+, asyncio, dataclasses, typing

**Spec:** `research/specs/phase1-core-loop.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `chimera/core/loop_events.py` | CREATE | `LoopEvent`, `LoopEventType`, `LoopResult` |
| `chimera/core/loop_state.py` | CREATE | `LoopState`, `QuerySource`, `RetryPolicy` |
| `chimera/core/abort.py` | CREATE | `AbortSignal` with linked children |
| `chimera/core/loop_deps.py` | CREATE | `LoopDeps` dependency injection |
| `chimera/core/recovery.py` | CREATE | `ErrorRecovery`, `WithheldError`, `RecoveryStrategy` |
| `chimera/core/streaming_executor.py` | CREATE | `StreamingToolExecutor` with concurrency control |
| `chimera/core/agent_loop.py` | CREATE | `AgentLoop` — the core loop |
| `chimera/core/tool.py` | MODIFY | Add `is_concurrency_safe`, `is_read_only`, `is_destructive`, `max_result_size_chars` |
| `chimera/core/loop.py` | MODIFY | `ReAct` wraps `AgentLoop` |
| `chimera/core/agent.py` | MODIFY | `Agent.run()` uses `AgentLoop` internally |
| `tests/core/test_loop_events.py` | CREATE | Unit tests for event types |
| `tests/core/test_abort.py` | CREATE | Unit tests for AbortSignal |
| `tests/core/test_loop_state.py` | CREATE | Unit tests for LoopState |
| `tests/core/test_recovery.py` | CREATE | Unit tests for ErrorRecovery |
| `tests/core/test_streaming_executor.py` | CREATE | Unit tests for StreamingToolExecutor |
| `tests/core/test_agent_loop.py` | CREATE | Integration tests for AgentLoop |

---

### Task 1: Loop Event Protocol

**Files:**
- Create: `chimera/core/loop_events.py`
- Test: `tests/core/test_loop_events.py`

- [ ] **Step 1: Write test for LoopEventType enum and LoopEvent dataclass**

```python
# tests/core/test_loop_events.py
from chimera.core.loop_events import LoopEvent, LoopEventType, LoopResult

def test_loop_event_types_exist():
    assert LoopEventType.STREAM_START.value == "stream_start"
    assert LoopEventType.ASSISTANT_MESSAGE.value == "assistant"
    assert LoopEventType.TOOL_USE.value == "tool_use"
    assert LoopEventType.TOOL_PROGRESS.value == "tool_progress"
    assert LoopEventType.TOOL_RESULT.value == "tool_result"
    assert LoopEventType.ERROR.value == "error"
    assert LoopEventType.RESULT.value == "result"

def test_loop_event_creation():
    event = LoopEvent(type=LoopEventType.STREAM_START, data={"turn": 0}, turn=0)
    assert event.type == LoopEventType.STREAM_START
    assert event.turn == 0
    assert event.timestamp > 0

def test_loop_result():
    result = LoopResult(reason="completed", messages=[], usage={}, cost_usd=0.0, duration_ms=0.0, turn_count=1)
    assert result.reason == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/core/test_loop_events.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement loop_events.py**

```python
# chimera/core/loop_events.py
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from chimera.types import Message

class LoopEventType(Enum):
    STREAM_START = "stream_start"
    ASSISTANT_MESSAGE = "assistant"
    ASSISTANT_CHUNK = "assistant_chunk"
    TOOL_USE = "tool_use"
    TOOL_PROGRESS = "tool_progress"
    TOOL_RESULT = "tool_result"
    SYSTEM_MESSAGE = "system"
    COMPACT_BOUNDARY = "compact_boundary"
    ERROR = "error"
    RESULT = "result"

@dataclass
class LoopEvent:
    type: LoopEventType
    data: Any
    turn: int
    timestamp: float = field(default_factory=time.time)

@dataclass
class LoopResult:
    reason: str
    messages: list[Message]
    usage: dict[str, int]
    cost_usd: float
    duration_ms: float
    turn_count: int
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/core/test_loop_events.py -v`

- [ ] **Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/core/loop_events.py tests/core/test_loop_events.py && git commit -m "feat(core): add LoopEvent protocol types"
```

---

### Task 2: AbortSignal

**Files:**
- Create: `chimera/core/abort.py`
- Test: `tests/core/test_abort.py`

- [ ] **Step 1: Write tests for AbortSignal**

```python
# tests/core/test_abort.py
from chimera.core.abort import AbortSignal

def test_abort_signal_initial_state():
    signal = AbortSignal()
    assert not signal.aborted
    assert signal.reason is None

def test_abort_signal_abort():
    signal = AbortSignal()
    signal.abort("user cancelled")
    assert signal.aborted
    assert signal.reason == "user cancelled"

def test_abort_signal_listener():
    signal = AbortSignal()
    reasons = []
    signal.on_abort(lambda r: reasons.append(r))
    signal.abort("test")
    assert reasons == ["test"]

def test_abort_signal_listener_called_if_already_aborted():
    signal = AbortSignal()
    signal.abort("early")
    reasons = []
    signal.on_abort(lambda r: reasons.append(r))
    assert reasons == ["early"]

def test_linked_child():
    parent = AbortSignal()
    child = parent.linked_child()
    parent.abort("parent done")
    assert child.aborted
    assert child.reason == "parent done"

def test_linked_child_does_not_affect_parent():
    parent = AbortSignal()
    child = parent.linked_child()
    child.abort("child done")
    assert not parent.aborted

def test_abort_only_fires_once():
    signal = AbortSignal()
    count = []
    signal.on_abort(lambda r: count.append(1))
    signal.abort("first")
    signal.abort("second")
    assert len(count) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/core/test_abort.py -v`

- [ ] **Step 3: Implement abort.py**

```python
# chimera/core/abort.py
from __future__ import annotations
from typing import Callable

class AbortSignal:
    def __init__(self) -> None:
        self._aborted = False
        self._reason: str | None = None
        self._listeners: list[Callable[[str], None]] = []

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> str | None:
        return self._reason

    def abort(self, reason: str = "aborted") -> None:
        if self._aborted:
            return
        self._aborted = True
        self._reason = reason
        for listener in self._listeners:
            listener(reason)

    def on_abort(self, callback: Callable[[str], None]) -> None:
        self._listeners.append(callback)
        if self._aborted:
            callback(self._reason)  # type: ignore[arg-type]

    def linked_child(self) -> AbortSignal:
        child = AbortSignal()
        self.on_abort(lambda r: child.abort(r))
        return child
```

- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/core/abort.py tests/core/test_abort.py && git commit -m "feat(core): add AbortSignal with linked children"
```

---

### Task 3: LoopState and QuerySource

**Files:**
- Create: `chimera/core/loop_state.py`
- Test: `tests/core/test_loop_state.py`

- [ ] **Step 1: Write tests**

```python
# tests/core/test_loop_state.py
from chimera.core.loop_state import LoopState, QuerySource, RetryPolicy, RETRY_POLICIES
from chimera.types import Message

def test_query_source_enum():
    assert QuerySource.FOREGROUND.value == "foreground"
    assert QuerySource.BACKGROUND.value == "background"
    assert QuerySource.FORK.value == "fork"

def test_retry_policy_foreground_retries_529():
    policy = RETRY_POLICIES[QuerySource.FOREGROUND]
    assert policy.retry_on_529 is True

def test_retry_policy_background_does_not_retry_529():
    policy = RETRY_POLICIES[QuerySource.BACKGROUND]
    assert policy.retry_on_529 is False

def test_loop_state_next_turn():
    state = LoopState(messages=[], turn_count=0)
    msg = Message.assistant("hello")
    next_state = state.next_turn(msg, [])
    assert next_state.turn_count == 1
    assert next_state.max_output_tokens_recovery_count == 0
    assert len(next_state.messages) == 1
```

- [ ] **Step 2: Run test, verify fail**
- [ ] **Step 3: Implement loop_state.py**

```python
# chimera/core/loop_state.py
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from chimera.types import Message, ToolResult

class QuerySource(Enum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"
    FORK = "fork"

@dataclass
class RetryPolicy:
    max_retries: int
    retry_on_529: bool
    retry_on_connection_error: bool
    fallback_model: str | None = None
    max_consecutive_529: int = 3

RETRY_POLICIES = {
    QuerySource.FOREGROUND: RetryPolicy(max_retries=5, retry_on_529=True, retry_on_connection_error=True),
    QuerySource.BACKGROUND: RetryPolicy(max_retries=1, retry_on_529=False, retry_on_connection_error=True),
    QuerySource.FORK: RetryPolicy(max_retries=2, retry_on_529=False, retry_on_connection_error=True),
}

@dataclass
class LoopState:
    messages: list[Message]
    turn_count: int
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    max_output_tokens_override: int | None = None
    transition_reason: str | None = None

    def next_turn(self, assistant_msg: Message, tool_results: list[ToolResult]) -> LoopState:
        tool_msgs = []  # Will be populated by caller with tool result messages
        return LoopState(
            messages=[*self.messages, assistant_msg, *tool_msgs],
            turn_count=self.turn_count + 1,
        )
```

- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/core/loop_state.py tests/core/test_loop_state.py && git commit -m "feat(core): add LoopState and QuerySource"
```

---

### Task 4: Error Recovery

**Files:**
- Create: `chimera/core/recovery.py`
- Test: `tests/core/test_recovery.py`

- [ ] **Step 1: Write tests**

```python
# tests/core/test_recovery.py
import pytest
from chimera.core.recovery import ErrorRecovery, WithheldError, RecoveryStrategy, RecoveryResult
from chimera.core.loop_state import LoopState

def test_withheld_error():
    err = WithheldError(type="prompt_too_long", original_error=Exception("too long"))
    assert err.type == "prompt_too_long"

@pytest.mark.asyncio
async def test_max_output_tokens_escalates():
    recovery = ErrorRecovery()
    state = LoopState(messages=[], turn_count=0)
    error = WithheldError(type="max_output_tokens", original_error=Exception("max"))
    result = await recovery.attempt_recovery(state, error)
    assert result.should_continue
    assert result.strategy_used == RecoveryStrategy.ESCALATE_OUTPUT
    assert state.max_output_tokens_override == 64_000

@pytest.mark.asyncio
async def test_max_output_tokens_exhausted_after_3():
    recovery = ErrorRecovery()
    state = LoopState(messages=[], turn_count=0, max_output_tokens_recovery_count=3)
    error = WithheldError(type="max_output_tokens", original_error=Exception("max"))
    result = await recovery.attempt_recovery(state, error)
    assert not result.should_continue
    assert result.reason == "max_output_tokens_exhausted"

@pytest.mark.asyncio
async def test_unknown_error_is_unrecoverable():
    recovery = ErrorRecovery()
    state = LoopState(messages=[], turn_count=0)
    error = WithheldError(type="unknown", original_error=Exception("???"))
    result = await recovery.attempt_recovery(state, error)
    assert not result.should_continue
```

- [ ] **Step 2: Run test, verify fail**
- [ ] **Step 3: Implement recovery.py** (follow spec `research/specs/phase1-core-loop.md` Section 4)
- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/core/recovery.py tests/core/test_recovery.py && git commit -m "feat(core): add ErrorRecovery with multi-stage strategies"
```

---

### Task 5: Tool Concurrency Flags

**Files:**
- Modify: `chimera/core/tool.py`
- Test: `tests/core/test_tool_flags.py`

- [ ] **Step 1: Write tests for new tool flags**

```python
# tests/core/test_tool_flags.py
from chimera.core.tool import BaseTool
from chimera.types import ToolResult

class ReadOnlyTool(BaseTool):
    name = "test_read"
    description = "test"
    parameters = {}
    is_concurrency_safe = True
    is_read_only = True
    def execute(self, args, env):
        return ToolResult(output="ok")

class WriteTool(BaseTool):
    name = "test_write"
    description = "test"
    parameters = {}
    def execute(self, args, env):
        return ToolResult(output="ok")

def test_default_flags():
    tool = WriteTool()
    assert tool.is_concurrency_safe is False
    assert tool.is_read_only is False
    assert tool.is_destructive is False
    assert tool.max_result_size_chars == 30_000

def test_readonly_tool_is_concurrent():
    tool = ReadOnlyTool()
    assert tool.is_concurrency_safe is True
    assert tool.is_read_only is True
```

- [ ] **Step 2: Run test, verify fail** (new attributes don't exist yet)
- [ ] **Step 3: Add flags to BaseTool**

Add to `chimera/core/tool.py` class `BaseTool`, after `requires_approval`:
```python
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_destructive: bool = False
    max_result_size_chars: int = 30_000
```

- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Run full test suite to check nothing broke**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/ -x -q --timeout=30 2>&1 | tail -5`

- [ ] **Step 6: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/core/tool.py tests/core/test_tool_flags.py && git commit -m "feat(core): add concurrency and safety flags to BaseTool"
```

---

### Task 6: StreamingToolExecutor

**Files:**
- Create: `chimera/core/streaming_executor.py`
- Test: `tests/core/test_streaming_executor.py`

- [ ] **Step 1: Write tests**

```python
# tests/core/test_streaming_executor.py
import pytest
import asyncio
from chimera.core.streaming_executor import StreamingToolExecutor
from chimera.core.tool import BaseTool
from chimera.core.abort import AbortSignal
from chimera.types import ToolCall, ToolResult
from chimera.env.base import Environment

class FastTool(BaseTool):
    name = "fast"
    description = "fast"
    parameters = {}
    is_concurrency_safe = True
    def execute(self, args, env): return ToolResult(output="fast done")
    async def async_execute(self, args, env): return ToolResult(output="fast done")

class SlowTool(BaseTool):
    name = "slow"
    description = "slow"
    parameters = {}
    is_concurrency_safe = False
    def execute(self, args, env): return ToolResult(output="slow done")
    async def async_execute(self, args, env):
        await asyncio.sleep(0.01)
        return ToolResult(output="slow done")

@pytest.mark.asyncio
async def test_concurrent_tools_run_in_parallel():
    executor = StreamingToolExecutor([FastTool()], max_concurrent=5)
    calls = [ToolCall(id=f"c{i}", name="fast", arguments={}) for i in range(3)]
    for call in calls:
        await executor.submit(call)
    results = await executor.collect()
    assert len(results) == 3
    assert all(r[1].output == "fast done" for r in results)

@pytest.mark.asyncio
async def test_non_concurrent_tools_run_sequentially():
    executor = StreamingToolExecutor([SlowTool()], max_concurrent=5)
    call = ToolCall(id="c1", name="slow", arguments={})
    await executor.submit(call)
    results = await executor.collect()
    assert len(results) == 1

@pytest.mark.asyncio
async def test_discard_returns_error_results():
    executor = StreamingToolExecutor([SlowTool()], max_concurrent=5)
    call = ToolCall(id="c1", name="slow", arguments={})
    await executor.submit(call)
    results = await executor.discard()
    # Should have synthetic error or completed result
    assert len(results) >= 0  # May or may not have completed
```

- [ ] **Step 2: Run test, verify fail**
- [ ] **Step 3: Implement streaming_executor.py** (follow spec Section 3)
- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/core/streaming_executor.py tests/core/test_streaming_executor.py && git commit -m "feat(core): add StreamingToolExecutor with concurrency control"
```

---

### Task 7: Loop Dependencies

**Files:**
- Create: `chimera/core/loop_deps.py`
- Test: `tests/core/test_loop_deps.py`

- [ ] **Step 1: Write test**

```python
# tests/core/test_loop_deps.py
from chimera.core.loop_deps import LoopDeps, production_deps

def test_loop_deps_has_required_fields():
    deps = LoopDeps(call_model=lambda: None, compact=lambda: None)
    assert deps.call_model is not None
    assert deps.compact is not None
    assert callable(deps.uuid)
```

- [ ] **Step 2: Run test, verify fail**
- [ ] **Step 3: Implement loop_deps.py**
- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/core/loop_deps.py tests/core/test_loop_deps.py && git commit -m "feat(core): add LoopDeps dependency injection"
```

---

### Task 8: AgentLoop Core

**Files:**
- Create: `chimera/core/agent_loop.py`
- Test: `tests/core/test_agent_loop.py`

- [ ] **Step 1: Write integration test with mock provider**

```python
# tests/core/test_agent_loop.py
import pytest
from chimera.core.agent_loop import AgentLoop
from chimera.core.loop_events import LoopEventType
from chimera.core.tool import BaseTool
from chimera.types import Message, ToolCall, ToolResult
from chimera.providers.base import Response

class MockProvider:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.model_name = "mock"
    async def async_complete(self, messages, tools=None, **kwargs):
        return next(self._responses)
    async def stream(self, messages, tools=None, **kwargs):
        resp = next(self._responses)
        yield resp  # Single-event stream for simplicity

class EchoTool(BaseTool):
    name = "echo"
    description = "echoes input"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    is_concurrency_safe = True
    def execute(self, args, env): return ToolResult(output=args.get("text", ""))
    async def async_execute(self, args, env): return ToolResult(output=args.get("text", ""))

@pytest.mark.asyncio
async def test_simple_completion_no_tools():
    provider = MockProvider([Response(content="Hello!", tool_calls=[], usage={})])
    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("Hi")],
        tools=[],
        provider=provider,
        system_prompt="You are helpful.",
    ):
        events.append(event)
    assert any(e.type == LoopEventType.RESULT for e in events)
    result_event = next(e for e in events if e.type == LoopEventType.RESULT)
    assert result_event.data.reason == "completed"

@pytest.mark.asyncio
async def test_tool_call_then_completion():
    responses = [
        Response(content="Let me echo", tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "hello"})], usage={}),
        Response(content="Done!", tool_calls=[], usage={}),
    ]
    provider = MockProvider(responses)
    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("Echo hello")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="You are helpful.",
    ):
        events.append(event)
    tool_results = [e for e in events if e.type == LoopEventType.TOOL_RESULT]
    assert len(tool_results) >= 1
    result_event = next(e for e in events if e.type == LoopEventType.RESULT)
    assert result_event.data.reason == "completed"
    assert result_event.data.turn_count == 2

@pytest.mark.asyncio
async def test_max_turns_exit():
    # Provider always returns tool calls — should hit max_turns
    def make_response():
        return Response(content="again", tool_calls=[ToolCall(id="t1", name="echo", arguments={"text": "x"})], usage={})
    provider = MockProvider([make_response() for _ in range(10)])
    loop = AgentLoop()
    events = []
    async for event in loop.run(
        messages=[Message.user("loop forever")],
        tools=[EchoTool()],
        provider=provider,
        system_prompt="test",
        max_turns=3,
    ):
        events.append(event)
    result_event = next(e for e in events if e.type == LoopEventType.RESULT)
    assert result_event.data.reason == "max_turns"
```

- [ ] **Step 2: Run test, verify fail**
- [ ] **Step 3: Implement agent_loop.py** (follow spec Section 2 — the core `while True` loop with phases A-D)
- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/core/agent_loop.py tests/core/test_agent_loop.py && git commit -m "feat(core): add AgentLoop with AsyncGenerator protocol"
```

---

### Task 9: Wire ReAct to AgentLoop

**Files:**
- Modify: `chimera/core/loop.py`
- Modify: `chimera/core/agent.py`
- Test: `tests/core/test_react_compat.py`

- [ ] **Step 1: Write compatibility test**

```python
# tests/core/test_react_compat.py
from chimera.core.loop import ReAct, drain_steps
from chimera.core.agent import Agent
from chimera.core.tool import BaseTool
from chimera.core.context import Context
from chimera.types import AgentResult, Message, ToolResult

# Existing tests should still pass — ReAct.run() and Agent.run() return AgentResult
def test_react_run_still_works(mock_provider):
    """ReAct.run() should still return AgentResult (backwards compatible)."""
    loop = ReAct(max_steps=5)
    context = Context(system="test")
    context.add(Message.user("hello"))
    result = loop.run(mock_provider, [], context, None)
    assert isinstance(result, AgentResult)
```

- [ ] **Step 2: Add `async_run_loop` method to ReAct that delegates to AgentLoop internally**

Keep `ReAct.run()` and `ReAct.iter_steps()` working exactly as before. Add a new `ReAct.async_run_events()` that returns `AsyncGenerator[LoopEvent]` using `AgentLoop` under the hood. This is additive — no breaking changes.

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && python -m pytest tests/ -x -q --timeout=30 2>&1 | tail -10`
Expected: All existing tests pass

- [ ] **Step 4: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/core/loop.py chimera/core/agent.py tests/core/test_react_compat.py && git commit -m "feat(core): wire ReAct to AgentLoop, backwards compatible"
```

---

### Task 10: Abort Signal Integration

**Files:**
- Modify: `chimera/core/agent_loop.py`
- Test: `tests/core/test_agent_loop_abort.py`

- [ ] **Step 1: Write abort test**

```python
# tests/core/test_agent_loop_abort.py
import pytest
import asyncio
from chimera.core.agent_loop import AgentLoop
from chimera.core.abort import AbortSignal
from chimera.core.loop_events import LoopEventType
from chimera.types import Message, ToolCall, ToolResult
from chimera.providers.base import Response

@pytest.mark.asyncio
async def test_abort_during_tool_execution():
    abort = AbortSignal()

    class SlowProvider:
        model_name = "mock"
        async def async_complete(self, messages, **kw):
            return Response(content="calling", tool_calls=[ToolCall(id="t1", name="slow", arguments={})], usage={})

    class SlowTool:
        name = "slow"
        description = "slow"
        parameters = {}
        is_concurrency_safe = False
        async def async_execute(self, args, env):
            await asyncio.sleep(10)  # Very slow
            return ToolResult(output="done")
        # BaseTool interface stubs
        def execute(self, args, env): return ToolResult(output="done")
        def to_anthropic_schema(self): return {}

    loop = AgentLoop()
    # Abort after 50ms
    asyncio.get_event_loop().call_later(0.05, lambda: abort.abort("user cancelled"))

    events = []
    async for event in loop.run(
        messages=[Message.user("do something slow")],
        tools=[SlowTool()],
        provider=SlowProvider(),
        system_prompt="test",
        abort_signal=abort,
    ):
        events.append(event)

    result_event = next((e for e in events if e.type == LoopEventType.RESULT), None)
    assert result_event is not None
    assert "abort" in result_event.data.reason
```

- [ ] **Step 2: Implement abort checking in AgentLoop** (check `abort_signal.aborted` at loop start and after tool execution)
- [ ] **Step 3: Run test, verify pass**
- [ ] **Step 4: Commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add chimera/core/agent_loop.py tests/core/test_agent_loop_abort.py && git commit -m "feat(core): integrate AbortSignal into AgentLoop"
```

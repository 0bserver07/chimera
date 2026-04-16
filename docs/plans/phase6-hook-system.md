# Phase 6: Hook System — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an event-driven hook system with 27 lifecycle events, three hook types (shell command, LLM prompt, function callback), async hook registry, multi-source loading with priority, and session-scoped hooks.

**Architecture:** `HookExecutor` runs hooks at each lifecycle point. `HookLoader` aggregates hooks from settings files + plugins + session. `AsyncHookRegistry` tracks background hooks. `SessionHookManager` handles ephemeral in-memory hooks.

**Tech Stack:** Python 3.11+, asyncio, subprocess, JSON settings

**Spec:** `research/specs/phase6-hook-system.md`

**Depends on:** Phase 1 (loop lifecycle), Phase 4 (PermissionRequest hook)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `chimera/hooks/events.py` | CREATE | `HookEvent` enum (27 events) |
| `chimera/hooks/types.py` | CREATE | `HookInput`, `HookOutput`, `CommandHook`, `PromptHook`, `FunctionHook`, `HookMatcher` |
| `chimera/hooks/executor.py` | CREATE | `HookExecutor` |
| `chimera/hooks/async_registry.py` | CREATE | `AsyncHookRegistry` |
| `chimera/hooks/session_hooks.py` | CREATE | `SessionHookManager` |
| `chimera/hooks/loader.py` | CREATE | `HookLoader` |
| `chimera/core/agent_loop.py` | MODIFY | Fire hooks at lifecycle points |
| `tests/hooks/test_events.py` | CREATE | |
| `tests/hooks/test_executor.py` | CREATE | |
| `tests/hooks/test_session_hooks.py` | CREATE | |
| `tests/hooks/test_loader.py` | CREATE | |

---

### Task 1: HookEvent Enum

- [ ] **Step 1: Write test for all 27 events**
- [ ] **Step 2-5: Implement events.py, test, commit**

---

### Task 2: Hook Types

- [ ] **Step 1: Write tests for HookInput, HookOutput, CommandHook, PromptHook, FunctionHook, HookMatcher**
- [ ] **Step 2-5: Implement types.py, test, commit**

---

### Task 3: HookExecutor — Command Hooks

- [ ] **Step 1: Write tests**

```python
# tests/hooks/test_executor.py
import pytest
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import HookInput, HookMatcher, CommandHook
from chimera.hooks.events import HookEvent

@pytest.mark.asyncio
async def test_command_hook_exit_0_allows():
    executor = HookExecutor()
    hook = CommandHook(command="exit 0")
    matcher = HookMatcher(hooks=[hook])
    input_data = HookInput(event=HookEvent.PRE_TOOL_USE, session_id="s1", tool_name="bash")
    result = await executor.execute(HookEvent.PRE_TOOL_USE, input_data, [matcher])
    assert result.continue_execution is True

@pytest.mark.asyncio
async def test_command_hook_exit_2_blocks():
    executor = HookExecutor()
    hook = CommandHook(command="echo 'blocked' >&2; exit 2")
    matcher = HookMatcher(hooks=[hook])
    input_data = HookInput(event=HookEvent.PRE_TOOL_USE, session_id="s1", tool_name="bash")
    result = await executor.execute(HookEvent.PRE_TOOL_USE, input_data, [matcher])
    assert result.continue_execution is False
    assert "blocked" in (result.stop_reason or "")
```

- [ ] **Step 2-5: Implement executor.py command hook execution, test, commit**

---

### Task 4: HookExecutor — Function Hooks

- [ ] **Step 1: Write tests**

```python
@pytest.mark.asyncio
async def test_function_hook_allows():
    executor = HookExecutor()
    hook = FunctionHook(callback=lambda msgs, sig: True, timeout=5, error_message="fail")
    matcher = HookMatcher(hooks=[hook])
    input_data = HookInput(event=HookEvent.STOP, session_id="s1")
    result = await executor.execute(HookEvent.STOP, input_data, [matcher])
    assert result.continue_execution is True

@pytest.mark.asyncio
async def test_function_hook_blocks():
    executor = HookExecutor()
    hook = FunctionHook(callback=lambda msgs, sig: False, timeout=5, error_message="check failed")
    matcher = HookMatcher(hooks=[hook])
    input_data = HookInput(event=HookEvent.STOP, session_id="s1")
    result = await executor.execute(HookEvent.STOP, input_data, [matcher])
    assert result.continue_execution is False

@pytest.mark.asyncio
async def test_function_hook_timeout():
    import asyncio
    async def slow(msgs, sig): await asyncio.sleep(100); return True
    executor = HookExecutor()
    hook = FunctionHook(callback=slow, timeout=0.01, error_message="timeout")
    matcher = HookMatcher(hooks=[hook])
    input_data = HookInput(event=HookEvent.STOP, session_id="s1")
    result = await executor.execute(HookEvent.STOP, input_data, [matcher])
    assert result.system_message is not None  # Timeout warning
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 5: HookExecutor — Matcher Filtering and Result Merging

- [ ] **Step 1: Write tests for matcher matching and merge logic (block wins over allow)**
- [ ] **Step 2-5: Implement, test, commit**

---

### Task 6: HookExecutor — PreToolUse Input Modification

- [ ] **Step 1: Write test that PreToolUse hook modifies tool input via `updated_input`**
- [ ] **Step 2-5: Implement, test, commit**

---

### Task 7: AsyncHookRegistry

- [ ] **Step 1: Write tests**

```python
@pytest.mark.asyncio
async def test_register_and_check_completed():
    registry = AsyncHookRegistry()
    task = asyncio.create_task(asyncio.sleep(0.01))
    registry.register("h1", "test_hook", HookEvent.POST_TOOL_USE, task)
    await asyncio.sleep(0.05)
    completed = await registry.check_completed()
    assert len(completed) == 1

@pytest.mark.asyncio
async def test_timeout_cancels_task():
    registry = AsyncHookRegistry()
    task = asyncio.create_task(asyncio.sleep(100))
    registry.register("h1", "slow", HookEvent.POST_TOOL_USE, task, timeout_ms=10)
    await asyncio.sleep(0.05)
    completed = await registry.check_completed()
    assert len(completed) == 1
    assert "timed out" in (completed[0].result.system_message or "")
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 8: SessionHookManager

- [ ] **Step 1: Write tests for add/remove/get**
- [ ] **Step 2-5: Implement, test, commit**

---

### Task 9: HookLoader

- [ ] **Step 1: Write tests with temp settings files containing hook configs**
- [ ] **Step 2-5: Implement, test, commit**

---

### Task 10: Integration — Wire Hooks into AgentLoop

- [ ] **Step 1: Fire `SESSION_START` at loop start**
- [ ] **Step 2: Fire `PRE_TOOL_USE` before each tool, apply `updated_input`**
- [ ] **Step 3: Fire `POST_TOOL_USE` after each tool**
- [ ] **Step 4: Fire `STOP` before returning `completed`**
- [ ] **Step 5: Fire `USER_PROMPT_SUBMIT` when user message is processed**
- [ ] **Step 6: Run full test suite**
- [ ] **Step 7: Commit**

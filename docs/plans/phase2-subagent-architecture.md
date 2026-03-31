# Phase 2: Sub-Agent Architecture — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three-tier context isolation for sub-agents, agent definition loading from disk, background agent support, and an AgentTool that the model uses to spawn sub-agents.

**Architecture:** `AgentContext` carries isolated state per-agent. `AgentSpawner` manages lifecycle. `AgentDefinition` loaded from `.chimera/agents/` YAML files. `TaskManager` tracks background agents.

**Tech Stack:** Python 3.11+, asyncio, dataclasses, PyYAML

**Spec:** `research/specs/phase2-subagent-architecture.md`

**Depends on:** Phase 1 (AgentLoop, AbortSignal, LoopEvent)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `chimera/core/agent_context.py` | CREATE | `AgentContext`, `IsolationLevel` |
| `chimera/core/agent_spawner.py` | CREATE | `AgentSpawner` |
| `chimera/core/agent_definition.py` | CREATE | `AgentDefinition`, `AgentDefinitionLoader` |
| `chimera/core/builtin_agents.py` | CREATE | `BUILTIN_AGENTS` dict |
| `chimera/core/task_manager.py` | CREATE | `BackgroundTask`, `TaskManager` |
| `chimera/tools/agent_tool.py` | CREATE | `AgentTool` |
| `chimera/tools/task_tools.py` | CREATE | `TaskListTool`, `TaskOutputTool`, `TaskStopTool` |
| `tests/core/test_agent_context.py` | CREATE | Context isolation tests |
| `tests/core/test_agent_spawner.py` | CREATE | Spawner tests |
| `tests/core/test_agent_definition.py` | CREATE | Definition loading tests |
| `tests/core/test_task_manager.py` | CREATE | Background task tests |
| `tests/tools/test_agent_tool.py` | CREATE | AgentTool tests |

---

### Task 1: AgentContext with Isolation

**Files:**
- Create: `chimera/core/agent_context.py`
- Test: `tests/core/test_agent_context.py`

- [ ] **Step 1: Write tests for isolation**

```python
# tests/core/test_agent_context.py
from chimera.core.agent_context import AgentContext, IsolationLevel
from chimera.core.abort import AbortSignal

def test_create_root_context():
    ctx = AgentContext.create_root(agent_id="root")
    assert ctx.agent_id == "root"
    assert ctx.parent_agent_id is None
    assert ctx.depth == 0

def test_full_isolation_clones_state():
    root = AgentContext.create_root(agent_id="root")
    child = AgentContext.create_child(root, IsolationLevel.FULL)
    assert child.parent_agent_id == "root"
    assert child.depth == 1
    assert child.file_state_cache is not root.file_state_cache

def test_full_isolation_set_app_state_is_noop():
    root = AgentContext.create_root(agent_id="root")
    state_changes = []
    root.set_app_state = lambda u: state_changes.append(u)
    child = AgentContext.create_child(root, IsolationLevel.FULL)
    child.set_app_state(lambda s: "change")
    assert len(state_changes) == 0  # No-op

def test_set_app_state_for_tasks_always_reaches_root():
    root = AgentContext.create_root(agent_id="root")
    task_changes = []
    root.set_app_state_for_tasks = lambda u: task_changes.append(u)
    child = AgentContext.create_child(root, IsolationLevel.FULL)
    child.set_app_state_for_tasks(lambda s: "task")
    assert len(task_changes) == 1

def test_abort_signal_linked():
    root = AgentContext.create_root(agent_id="root")
    child = AgentContext.create_child(root, IsolationLevel.FULL)
    root.abort_signal.abort("parent done")
    assert child.abort_signal.aborted

def test_child_abort_does_not_affect_parent():
    root = AgentContext.create_root(agent_id="root")
    child = AgentContext.create_child(root, IsolationLevel.FULL)
    child.abort_signal.abort("child done")
    assert not root.abort_signal.aborted
```

- [ ] **Step 2: Run test, verify fail**
- [ ] **Step 3: Implement agent_context.py** (follow spec Section 1)
- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

---

### Task 2: AgentDefinition and Loader

**Files:**
- Create: `chimera/core/agent_definition.py`
- Test: `tests/core/test_agent_definition.py`

- [ ] **Step 1: Write tests**

```python
# tests/core/test_agent_definition.py
import tempfile
from pathlib import Path
from chimera.core.agent_definition import AgentDefinition, AgentDefinitionLoader

def test_from_dict():
    defn = AgentDefinition.from_dict({"name": "test", "description": "test agent", "model": "sonnet"})
    assert defn.name == "test"
    assert defn.model == "sonnet"

def test_from_yaml_file():
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        f.write("name: explorer\ndescription: explore code\ntools:\n  - read_file\n  - grep\n")
        f.flush()
        defn = AgentDefinition.from_file(Path(f.name))
    assert defn.name == "explorer"
    assert defn.tools == ["read_file", "grep"]

def test_loader_finds_agents_in_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        agents_dir = Path(tmpdir) / ".chimera" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "runner.yaml").write_text("name: runner\ndescription: run tests\n")
        loader = AgentDefinitionLoader([Path(tmpdir)])
        agents = loader.load_all()
        assert "runner" in agents
```

- [ ] **Step 2: Run test, verify fail**
- [ ] **Step 3: Implement agent_definition.py**
- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

---

### Task 3: Built-in Agents

**Files:**
- Create: `chimera/core/builtin_agents.py`
- Test: `tests/core/test_builtin_agents.py`

- [ ] **Step 1: Write test**

```python
from chimera.core.builtin_agents import BUILTIN_AGENTS

def test_builtin_agents_exist():
    assert "general-purpose" in BUILTIN_AGENTS
    assert "explore" in BUILTIN_AGENTS
    assert "plan" in BUILTIN_AGENTS

def test_explore_agent_is_readonly():
    explore = BUILTIN_AGENTS["explore"]
    assert "read_file" in explore.tools
    assert "write_file" not in (explore.tools or [])
```

- [ ] **Step 2: Run test, verify fail**
- [ ] **Step 3: Implement builtin_agents.py**
- [ ] **Step 4: Run test, verify pass**
- [ ] **Step 5: Commit**

---

### Task 4: TaskManager for Background Agents

**Files:**
- Create: `chimera/core/task_manager.py`
- Test: `tests/core/test_task_manager.py`

- [ ] **Step 1: Write tests**

```python
# tests/core/test_task_manager.py
import pytest
import asyncio
from chimera.core.task_manager import TaskManager, BackgroundTask

@pytest.mark.asyncio
async def test_register_and_list():
    mgr = TaskManager()
    task = BackgroundTask(task_id="t1", agent_id="a1", description="test")
    coro = asyncio.create_task(asyncio.sleep(100))
    mgr.register(task, coro)
    assert len(mgr.list_tasks()) == 1
    coro.cancel()
    try: await coro
    except asyncio.CancelledError: pass

@pytest.mark.asyncio
async def test_stop_task():
    mgr = TaskManager()
    task = BackgroundTask(task_id="t1", agent_id="a1", description="test")
    coro = asyncio.create_task(asyncio.sleep(100))
    mgr.register(task, coro)
    await mgr.stop("t1")
    assert mgr.get("t1").status == "stopped"
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 5: AgentSpawner

**Files:**
- Create: `chimera/core/agent_spawner.py`
- Test: `tests/core/test_agent_spawner.py`

- [ ] **Step 1: Write tests**

```python
# tests/core/test_agent_spawner.py
import pytest
from chimera.core.agent_spawner import AgentSpawner
from chimera.core.agent_context import AgentContext
from chimera.core.agent_definition import AgentDefinition
from chimera.core.loop_events import LoopEventType

@pytest.mark.asyncio
async def test_spawn_foreground_yields_events(mock_provider):
    spawner = AgentSpawner(provider_factory=lambda m: mock_provider)
    defn = AgentDefinition(name="test", description="test")
    root = AgentContext.create_root(agent_id="root")
    events = []
    async for event in spawner.spawn(defn, "do something", root):
        events.append(event)
    assert any(e.type == LoopEventType.RESULT for e in events)
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 6: AgentTool

**Files:**
- Create: `chimera/tools/agent_tool.py`
- Test: `tests/tools/test_agent_tool.py`

- [ ] **Step 1: Write tests**

```python
# tests/tools/test_agent_tool.py
import pytest
from chimera.tools.agent_tool import AgentTool

def test_agent_tool_schema():
    tool = AgentTool.__new__(AgentTool)
    assert tool.name == "agent"
    schema = tool.parameters
    assert "prompt" in schema["properties"]
    assert "description" in schema["properties"]
    assert "subagent_type" in schema["properties"]
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 7: Task Tools (TaskList, TaskOutput, TaskStop)

**Files:**
- Create: `chimera/tools/task_tools.py`
- Test: `tests/tools/test_task_tools.py`

- [ ] **Step 1: Write tests for each tool**
- [ ] **Step 2-5: Implement, test, commit**

---

### Task 8: Integration — Full Agent Spawning Flow

**Files:**
- Test: `tests/integration/test_agent_spawning.py`

- [ ] **Step 1: Write integration test that spawns a sub-agent, checks isolation, collects result**
- [ ] **Step 2: Run test, verify pass**
- [ ] **Step 3: Run full test suite**
- [ ] **Step 4: Commit**

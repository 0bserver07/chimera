# Phase 8: Production Infrastructure — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add feature flags, analytics with PII protection, MCP server lifecycle management, IDE bridge protocol, plugin system with full lifecycle, coordinator mode, and persistent memory.

**Architecture:** `FeatureFlags` for build-time/runtime gating. `AnalyticsManager` with sink pattern. `MCPServerLifecycle` with memoized connections. `BridgeProtocol` for IDE comms. `PluginManager` with full lifecycle. `CoordinatorMode` for multi-agent dispatch. `PersistentMemory` for cross-session notes.

**Tech Stack:** Python 3.11+, asyncio, websockets (optional), dataclasses

**Spec:** `research/specs/phase8-production-infrastructure.md`

**Depends on:** All previous phases

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `chimera/core/feature_flags.py` | CREATE | `FeatureFlags`, `STANDARD_FLAGS` |
| `chimera/analytics/manager.py` | CREATE | `AnalyticsManager`, `AnalyticsSink` |
| `chimera/analytics/sinks.py` | CREATE | `FileSink`, `StdoutSink` |
| `chimera/mcp/lifecycle.py` | CREATE | `MCPServerLifecycle` |
| `chimera/coordinator/mode.py` | CREATE | `CoordinatorMode` |
| `chimera/bridge/protocol.py` | CREATE | `BridgeProtocol`, `BridgeTransport` |
| `chimera/bridge/transports.py` | CREATE | `StdioBridgeTransport` |
| `chimera/core/memory.py` | CREATE | `PersistentMemory` |
| `chimera/plugins/manager.py` | MODIFY | Full lifecycle with `PluginRegistry` |
| `chimera/plugins/base.py` | MODIFY | Update `ChimeraPlugin` |
| `tests/core/test_feature_flags.py` | CREATE | |
| `tests/analytics/test_manager.py` | CREATE | |
| `tests/mcp/test_lifecycle.py` | CREATE | |
| `tests/coordinator/test_mode.py` | CREATE | |
| `tests/bridge/test_protocol.py` | CREATE | |
| `tests/core/test_memory.py` | CREATE | |
| `tests/plugins/test_manager.py` | CREATE | |

---

### Task 1: Feature Flags

- [ ] **Step 1: Write tests**

```python
# tests/core/test_feature_flags.py
import os
from chimera.core.feature_flags import FeatureFlags, STANDARD_FLAGS

def test_default_disabled():
    FeatureFlags._flags.clear()
    FeatureFlags._runtime_overrides.clear()
    assert FeatureFlags.enabled("NONEXISTENT") is False

def test_set_and_check():
    FeatureFlags.set("TEST_FLAG", True)
    assert FeatureFlags.enabled("TEST_FLAG") is True
    FeatureFlags._flags.clear()

def test_runtime_override_wins():
    FeatureFlags.set("X", False)
    FeatureFlags.override("X", True)
    assert FeatureFlags.enabled("X") is True
    FeatureFlags._flags.clear()
    FeatureFlags._runtime_overrides.clear()

def test_from_env(monkeypatch):
    monkeypatch.setenv("CHIMERA_FEATURE_PROACTIVE", "1")
    FeatureFlags._runtime_overrides.clear()
    FeatureFlags.from_env()
    assert FeatureFlags.enabled("PROACTIVE") is True
    FeatureFlags._runtime_overrides.clear()

def test_standard_flags_defined():
    assert "PROACTIVE" in STANDARD_FLAGS
    assert "COORDINATOR_MODE" in STANDARD_FLAGS
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 2: Analytics Manager

- [ ] **Step 1: Write tests**

```python
# tests/analytics/test_manager.py
import pytest
from chimera.analytics.manager import AnalyticsManager, AnalyticsSink, AnalyticsEvent

class MockSink(AnalyticsSink):
    def __init__(self):
        self.events = []
    async def log(self, event):
        self.events.append(event)

def test_queues_before_sink():
    mgr = AnalyticsManager()
    mgr.log_event("test_event", key="value")
    assert len(mgr._queue) == 1

@pytest.mark.asyncio
async def test_drains_on_attach():
    mgr = AnalyticsManager()
    mgr.log_event("e1")
    sink = MockSink()
    mgr.attach_sink(sink)
    import asyncio; await asyncio.sleep(0.05)
    assert mgr._queue == []

def test_strips_proto_fields():
    mgr = AnalyticsManager()
    mgr.log_event("test", safe="ok", _PROTO_pii="secret")
    event = mgr._queue[0]
    assert "_PROTO_pii" not in event.metadata
    assert "safe" in event.metadata
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 3: Analytics Sinks

- [ ] **Step 1: Write tests for FileSink and StdoutSink**
- [ ] **Step 2-5: Implement, test, commit**

---

### Task 4: MCP Server Lifecycle

- [ ] **Step 1: Write tests**

```python
# tests/mcp/test_lifecycle.py
import pytest
from chimera.mcp.lifecycle import MCPServerLifecycle

@pytest.mark.asyncio
async def test_memoized_connection():
    lifecycle = MCPServerLifecycle()
    # Two connects with same config should return same client
    config = {"url": "http://localhost:3000", "name": "test"}
    client1 = await lifecycle.connect(config)
    client2 = await lifecycle.connect(config)
    assert client1 is client2

@pytest.mark.asyncio
async def test_cleanup_agent_disconnects_owned():
    lifecycle = MCPServerLifecycle()
    config = {"url": "http://localhost:3001", "name": "agent_specific"}
    await lifecycle.connect_for_agent(config, "agent1")
    await lifecycle.cleanup_agent("agent1")
    # Connection should be removed
    assert lifecycle._cache_key(config) not in lifecycle._connections
```

- [ ] **Step 2-5: Implement (mock MCP connections for tests), test, commit**

---

### Task 5: Coordinator Mode

- [ ] **Step 1: Write tests**

```python
# tests/coordinator/test_mode.py
import pytest
from chimera.coordinator.mode import CoordinatorMode
from chimera.core.feature_flags import FeatureFlags

def test_disabled_by_default():
    FeatureFlags._flags.clear()
    FeatureFlags._runtime_overrides.clear()
    coord = CoordinatorMode(spawner=None, agent_definitions={})
    assert coord.is_enabled is False

def test_enabled_with_flag():
    FeatureFlags.override("COORDINATOR_MODE", True)
    coord = CoordinatorMode(spawner=None, agent_definitions={})
    assert coord.is_enabled is True
    FeatureFlags._runtime_overrides.clear()
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 6: Bridge Protocol

- [ ] **Step 1: Write tests**

```python
# tests/bridge/test_protocol.py
import pytest
from chimera.bridge.protocol import BridgeProtocol
from chimera.bridge.transports import InMemoryTransport  # For testing

@pytest.mark.asyncio
async def test_send_and_receive():
    transport = InMemoryTransport()
    protocol = BridgeProtocol(transport)
    received = []
    protocol.on_message("test_msg", lambda data: received.append(data))
    await transport.inject({"type": "test_msg", "data": {"key": "value"}})
    # Process one message
    async for msg in transport.receive():
        handler = protocol._handlers.get(msg["type"])
        if handler: await handler(msg["data"])
        break
    assert received == [{"key": "value"}]
```

- [ ] **Step 2-5: Implement protocol.py and InMemoryTransport (for tests) + StdioBridgeTransport, test, commit**

---

### Task 7: Persistent Memory

- [ ] **Step 1: Write tests**

```python
# tests/core/test_memory.py
import tempfile
from pathlib import Path
from chimera.core.memory import PersistentMemory

def test_write_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        mem = PersistentMemory(Path(tmpdir))
        mem.write("# Notes\nImportant thing")
        content = mem.load()
        assert "Important" in content

def test_append():
    with tempfile.TemporaryDirectory() as tmpdir:
        mem = PersistentMemory(Path(tmpdir))
        mem.write("Line 1")
        mem.append("Line 2")
        content = mem.load()
        assert "Line 1" in content
        assert "Line 2" in content

def test_truncation_at_200_lines():
    with tempfile.TemporaryDirectory() as tmpdir:
        mem = PersistentMemory(Path(tmpdir))
        mem.write("\n".join(f"Line {i}" for i in range(300)))
        content = mem.load()
        assert "truncated" in content
        assert content.count("\n") <= 201
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 8: Plugin Manager Rewrite

- [ ] **Step 1: Write tests for PluginRegistry (register tools, commands, hooks)**
- [ ] **Step 2: Write tests for PluginManager (load, enable, disable)**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run existing plugin tests for backwards compat**
- [ ] **Step 5: Commit**

---

### Task 9: Integration — Wire Everything Together

- [ ] **Step 1: `Agent.__init__()` loads feature flags from env**
- [ ] **Step 2: Analytics singleton initialized at startup**
- [ ] **Step 3: MCP lifecycle managed per-session**
- [ ] **Step 4: Persistent memory loaded into system prompt**
- [ ] **Step 5: Plugin manager loads at startup, feeds tools/commands/hooks to Agent**
- [ ] **Step 6: Run full test suite**
- [ ] **Step 7: Commit**

---

### Task 10: Final Integration Test

- [ ] **Step 1: Write end-to-end test**

```python
# tests/integration/test_full_stack.py
import pytest
from chimera.core.agent import Agent
from chimera.core.agent_loop import AgentLoop
from chimera.core.loop_events import LoopEventType

@pytest.mark.asyncio
async def test_full_stack_agent_run():
    """End-to-end: Agent with all Phase 1-8 components wired together."""
    # This test verifies that:
    # 1. AgentLoop runs (Phase 1)
    # 2. Tools have concurrency flags (Phase 1)
    # 3. Permissions check before tools (Phase 4)
    # 4. Hooks fire at lifecycle points (Phase 6)
    # 5. Commands are registered (Phase 7)
    # 6. Feature flags gate features (Phase 8)
    # 7. Analytics events are logged (Phase 8)
    agent = Agent(provider=mock_provider, tools=[...])
    result = agent.run("Write hello.py", env=local_env)
    assert result.success
```

- [ ] **Step 2: Run test, verify pass**
- [ ] **Step 3: Run full test suite — ALL 2774+ existing tests must pass**
- [ ] **Step 4: Final commit**

```bash
cd /Users/yadkonrad/dev_dev/year26/feb26/chimera && git add -A && git commit -m "feat: Phase 8 complete — all Claude Code architecture layers implemented"
```

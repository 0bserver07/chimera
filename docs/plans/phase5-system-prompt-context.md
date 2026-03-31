# Phase 5: System Prompt & Context — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add layered system prompt construction, cache-safe params for forked agents, API-based token estimation with fallback, tool deferral (ToolSearch), and compaction integration.

**Architecture:** `SystemPromptBuilder` assembles prompt in layers. `CacheSafeParams` captures exact prefix for forks. `TokenEstimator` counts via API with fallback. `ToolPool` manages eager vs deferred tools. `CompactionIntegration` hooks into the loop.

**Tech Stack:** Python 3.11+, asyncio, dataclasses

**Spec:** `research/specs/phase5-system-prompt-context.md`

**Depends on:** Phase 1, Phase 3

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `chimera/core/system_prompt.py` | CREATE | `SystemPromptBuilder`, `SystemPrompt`, `PromptLayer` |
| `chimera/core/context_assembler.py` | CREATE | `ContextAssembler` |
| `chimera/core/cache_safe_params.py` | CREATE | `CacheSafeParams`, `CacheSafeParamsStore` |
| `chimera/core/token_estimator.py` | CREATE | `TokenEstimator` |
| `chimera/core/tool_pool.py` | CREATE | `ToolPool`, `DeferredToolConfig` |
| `chimera/tools/tool_search.py` | CREATE | `ToolSearchTool` |
| `chimera/core/compaction_integration.py` | CREATE | `CompactionIntegration` |
| `tests/core/test_system_prompt.py` | CREATE | |
| `tests/core/test_context_assembler.py` | CREATE | |
| `tests/core/test_token_estimator.py` | CREATE | |
| `tests/core/test_tool_pool.py` | CREATE | |
| `tests/tools/test_tool_search.py` | CREATE | |

---

### Task 1: SystemPrompt and Builder

- [ ] **Step 1: Write tests**

```python
# tests/core/test_system_prompt.py
from chimera.core.system_prompt import SystemPromptBuilder, SystemPrompt, PromptLayer

def test_build_prompt():
    builder = SystemPromptBuilder()
    builder.add_layer("default", "You are helpful.")
    builder.add_layer("tools", "Available: bash, read")
    prompt = builder.build()
    assert len(prompt.layers) == 2
    assert "helpful" in prompt.to_string()

def test_cache_prefix_excludes_non_cacheable():
    builder = SystemPromptBuilder()
    builder.add_layer("default", "stable", cacheable=True)
    builder.add_layer("git_status", "dynamic", cacheable=False)
    prompt = builder.build()
    assert "stable" in prompt.cache_prefix()
    assert "dynamic" not in prompt.cache_prefix()

def test_to_api_messages():
    builder = SystemPromptBuilder()
    builder.add_layer("default", "You are helpful.", cacheable=True)
    builder.add_layer("context", "Working dir: /tmp", cacheable=False)
    prompt = builder.build()
    messages = prompt.to_api_messages()
    assert len(messages) == 2
    assert messages[0]["cache_control"] == {"type": "ephemeral"}
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 2: ContextAssembler

- [ ] **Step 1: Write tests**

```python
# tests/core/test_context_assembler.py
import pytest, tempfile
from pathlib import Path
from chimera.core.context_assembler import ContextAssembler

@pytest.mark.asyncio
async def test_assembles_default_prompt():
    with tempfile.TemporaryDirectory() as tmpdir:
        assembler = ContextAssembler(project_dir=Path(tmpdir), tools=[], model="test")
        prompt = await assembler.assemble()
        assert len(prompt.layers) >= 1  # At least default

@pytest.mark.asyncio
async def test_loads_chimera_md():
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "CHIMERA.md").write_text("# Project rules\nAlways use TDD")
        assembler = ContextAssembler(project_dir=Path(tmpdir), tools=[], model="test")
        prompt = await assembler.assemble()
        assert any("TDD" in layer.content for layer in prompt.layers)

@pytest.mark.asyncio
async def test_agent_override_with_fallback():
    from chimera.core.agent_definition import AgentDefinition
    with tempfile.TemporaryDirectory() as tmpdir:
        defn = AgentDefinition(name="test", description="test", system_prompt="Custom prompt")
        assembler = ContextAssembler(project_dir=Path(tmpdir), tools=[], model="test")
        prompt = await assembler.assemble(agent_definition=defn)
        assert any("Custom" in layer.content for layer in prompt.layers)
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 3: TokenEstimator

- [ ] **Step 1: Write tests (with mock provider)**
- [ ] **Step 2-5: Implement, test, commit**

---

### Task 4: ToolPool and ToolSearchTool

- [ ] **Step 1: Write tests**

```python
# tests/core/test_tool_pool.py
from chimera.core.tool_pool import ToolPool, DeferredToolConfig

def test_all_tools_eager_when_under_limit():
    tools = [MockTool(f"t{i}") for i in range(10)]
    pool = ToolPool(tools, DeferredToolConfig(max_eager_tools=30))
    assert len(pool.get_eager_tools()) == 10

def test_defers_tools_over_limit():
    tools = [MockTool(f"t{i}") for i in range(40)]
    config = DeferredToolConfig(max_eager_tools=10, always_eager={"t0", "t1"})
    pool = ToolPool(tools, config)
    eager = pool.get_eager_tools()
    assert len(eager) <= 11  # always_eager + ToolSearchTool
    assert any(t.name == "tool_search" for t in eager)

# tests/tools/test_tool_search.py
import pytest
from chimera.tools.tool_search import ToolSearchTool

@pytest.mark.asyncio
async def test_search_finds_matching_tools():
    tools = [MockTool("read_file", description="Read files"), MockTool("bash", description="Run shell")]
    search = ToolSearchTool(tools)
    result = await search.async_execute({"query": "read"}, None)
    assert "read_file" in result.output
    assert "bash" not in result.output
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 5: CompactionIntegration

- [ ] **Step 1: Write tests**
- [ ] **Step 2-5: Implement, test, commit**

---

### Task 6: CacheSafeParams

- [ ] **Step 1: Write tests**

```python
from chimera.core.cache_safe_params import CacheSafeParams, CacheSafeParamsStore

def test_save_and_get():
    params = CacheSafeParams(system_prompt=..., tools=[], messages=[], model="test", max_output_tokens=4096)
    CacheSafeParamsStore.save(params)
    assert CacheSafeParamsStore.get() is params

def test_matches():
    p1 = CacheSafeParams(system_prompt=prompt1, tools=[t1], messages=[], model="test", max_output_tokens=4096)
    p2 = CacheSafeParams(system_prompt=prompt1, tools=[t1], messages=[], model="test", max_output_tokens=4096)
    assert p1.matches(p2)
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 7: Integration — Wire into AgentLoop

- [ ] **Step 1: `AgentLoop.run()` accepts `SystemPrompt` instead of plain string**
- [ ] **Step 2: Auto-compact integration: call `CompactionIntegration.auto_compact_if_needed()` at start of each turn**
- [ ] **Step 3: Use `ToolPool.get_eager_tools()` for API calls, `ToolPool.get_all_tools()` for execution**
- [ ] **Step 4: Run full test suite**
- [ ] **Step 5: Commit**

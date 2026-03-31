# Phase 4: Permission System Hardening — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace chimera's simple `ApprovalPolicy`/`PermissionPolicy` with a multi-source rule system supporting the `ToolName(content)` pattern format, five permission modes, bypass-immune safety checks, denial tracking, and sandboxed shell execution.

**Architecture:** `PermissionChecker` implements the step-by-step decision algorithm from Claude Code. `PermissionRuleLoader` loads rules from user/project/local settings. `DenialTrackingState` tracks repeated denials. `SandboxAdapter` wraps shell execution.

**Tech Stack:** Python 3.11+, fnmatch, dataclasses, JSON settings files

**Spec:** `research/specs/phase4-permission-system.md`

**Depends on:** Phase 1 (tool execution integrates permission checks)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `chimera/permissions/modes.py` | CREATE | `PermissionMode` enum |
| `chimera/permissions/rules.py` | CREATE | `PermissionRuleValue`, `PermissionRule`, `RuleSource`, `PermissionBehavior` |
| `chimera/permissions/decisions.py` | CREATE | `PermissionDecision`, `DecisionReason` |
| `chimera/permissions/context.py` | CREATE | `PermissionContext` (frozen at turn start) |
| `chimera/permissions/checker.py` | CREATE | `PermissionChecker` (core algorithm) |
| `chimera/permissions/loader.py` | CREATE | `PermissionRuleLoader` |
| `chimera/permissions/denial_tracking.py` | CREATE | `DenialTrackingState` |
| `chimera/permissions/interactive.py` | CREATE | `InteractivePermissionHandler` |
| `chimera/permissions/sandbox.py` | CREATE | `SandboxAdapter`, `SandboxConfig` |
| `chimera/core/tool.py` | MODIFY | Add `get_permission_content()`, `check_permissions()` |
| `chimera/tools/bash.py` | MODIFY | Implement `get_permission_content()` |
| `chimera/tools/write.py` | MODIFY | Implement `get_permission_content()` |
| `tests/permissions/test_rules.py` | CREATE | Rule parsing and matching |
| `tests/permissions/test_checker.py` | CREATE | Decision algorithm |
| `tests/permissions/test_loader.py` | CREATE | Settings loading |
| `tests/permissions/test_denial_tracking.py` | CREATE | Denial counting |

---

### Task 1: Permission Modes and Behaviors

- [ ] **Step 1: Write tests**

```python
# tests/permissions/test_modes.py
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import PermissionBehavior

def test_permission_modes():
    assert PermissionMode.DEFAULT.value == "default"
    assert PermissionMode.BYPASS.value == "bypass_permissions"
    assert PermissionMode.DONT_ASK.value == "dont_ask"

def test_permission_behaviors():
    assert PermissionBehavior.ALLOW.value == "allow"
    assert PermissionBehavior.DENY.value == "deny"
    assert PermissionBehavior.ASK.value == "ask"
```

- [ ] **Step 2-5: Implement modes.py, rules.py enums, test, commit**

---

### Task 2: Rule Parsing — `ToolName(content)` Format

- [ ] **Step 1: Write tests**

```python
# tests/permissions/test_rules.py
from chimera.permissions.rules import PermissionRuleValue

def test_parse_simple_tool():
    rule = PermissionRuleValue.from_string("bash")
    assert rule.tool_name == "bash"
    assert rule.content is None

def test_parse_tool_with_content():
    rule = PermissionRuleValue.from_string("bash(git *)")
    assert rule.tool_name == "bash"
    assert rule.content == "git *"

def test_parse_escaped_parens():
    rule = PermissionRuleValue.from_string(r"bash(echo \(hello\))")
    assert rule.content == "echo (hello)"

def test_to_string_roundtrip():
    rule = PermissionRuleValue(tool_name="bash", content="git *")
    assert PermissionRuleValue.from_string(rule.to_string()).content == "git *"

def test_matches_blanket():
    rule = PermissionRuleValue(tool_name="bash", content=None)
    assert rule.matches("bash") is True
    assert rule.matches("read_file") is False

def test_matches_with_pattern():
    rule = PermissionRuleValue(tool_name="bash", content="git *")
    assert rule.matches("bash", "git push") is True
    assert rule.matches("bash", "rm -rf /") is False

def test_matches_mcp_server_prefix():
    rule = PermissionRuleValue(tool_name="mcp__myserver", content=None)
    assert rule.matches("mcp__myserver__tool1") is True
    assert rule.matches("mcp__other__tool1") is False
```

- [ ] **Step 2-5: Implement rules.py with parsing, matching, test, commit**

---

### Task 3: PermissionDecision Types

- [ ] **Step 1: Write tests**
- [ ] **Step 2-5: Implement decisions.py, test, commit**

---

### Task 4: PermissionContext (Frozen)

- [ ] **Step 1: Write tests**

```python
# tests/permissions/test_context.py
from chimera.permissions.context import PermissionContext
from chimera.permissions.modes import PermissionMode

def test_context_is_frozen():
    ctx = PermissionContext(mode=PermissionMode.DEFAULT, allow_rules={}, deny_rules={}, ask_rules={}, additional_working_dirs=frozenset(), is_bypass_available=True)
    # Should not be mutable
    import dataclasses
    assert dataclasses.is_dataclass(ctx)
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 5: PermissionChecker (Core Algorithm)

- [ ] **Step 1: Write tests for each step**

```python
# tests/permissions/test_checker.py
import pytest
from chimera.permissions.checker import PermissionChecker
from chimera.permissions.context import PermissionContext
from chimera.permissions.modes import PermissionMode
from chimera.permissions.rules import PermissionBehavior, RuleSource

@pytest.mark.asyncio
async def test_deny_rule_blocks():
    ctx = PermissionContext(
        mode=PermissionMode.DEFAULT,
        allow_rules={}, deny_rules={RuleSource.USER: ["bash"]}, ask_rules={},
        additional_working_dirs=frozenset(), is_bypass_available=True,
    )
    checker = PermissionChecker()
    decision = await checker.check(MockTool("bash"), {}, ctx)
    assert decision.behavior == PermissionBehavior.DENY

@pytest.mark.asyncio
async def test_allow_rule_allows():
    ctx = PermissionContext(
        mode=PermissionMode.DEFAULT,
        allow_rules={RuleSource.USER: ["bash"]}, deny_rules={}, ask_rules={},
        additional_working_dirs=frozenset(), is_bypass_available=True,
    )
    checker = PermissionChecker()
    decision = await checker.check(MockTool("bash"), {}, ctx)
    assert decision.behavior == PermissionBehavior.ALLOW

@pytest.mark.asyncio
async def test_bypass_mode_allows_all():
    ctx = PermissionContext(
        mode=PermissionMode.BYPASS,
        allow_rules={}, deny_rules={}, ask_rules={},
        additional_working_dirs=frozenset(), is_bypass_available=True,
    )
    checker = PermissionChecker()
    decision = await checker.check(MockTool("bash"), {}, ctx)
    assert decision.behavior == PermissionBehavior.ALLOW

@pytest.mark.asyncio
async def test_no_rule_defaults_to_ask():
    ctx = PermissionContext(
        mode=PermissionMode.DEFAULT,
        allow_rules={}, deny_rules={}, ask_rules={},
        additional_working_dirs=frozenset(), is_bypass_available=True,
    )
    checker = PermissionChecker()
    decision = await checker.check(MockTool("bash"), {"command": "rm -rf /"}, ctx)
    assert decision.behavior == PermissionBehavior.ASK
```

- [ ] **Step 2-5: Implement checker.py following spec Section 5, test, commit**

---

### Task 6: PermissionRuleLoader

- [ ] **Step 1: Write tests with temp settings files**
- [ ] **Step 2-5: Implement, test, commit**

---

### Task 7: DenialTrackingState

- [ ] **Step 1: Write tests**

```python
from chimera.permissions.denial_tracking import DenialTrackingState

def test_tracks_denials():
    state = DenialTrackingState(max_denials=3)
    assert not state.should_auto_deny("bash")
    state.record_denial("bash")
    state.record_denial("bash")
    assert not state.should_auto_deny("bash")
    state.record_denial("bash")
    assert state.should_auto_deny("bash")
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 8: Tool Permission Content Extraction

- [ ] **Step 1: Add `get_permission_content()` to BaseTool**
- [ ] **Step 2: Override in BashTool to return command string**
- [ ] **Step 3: Override in WriteFileTool to return file path**
- [ ] **Step 4: Run existing tests, verify no breakage**
- [ ] **Step 5: Commit**

---

### Task 9: SandboxAdapter

- [ ] **Step 1: Write tests for SandboxConfig, ALWAYS_DENY paths**
- [ ] **Step 2-5: Implement sandbox.py, test, commit**

---

### Task 10: Integration — Wire into AgentLoop

- [ ] **Step 1: Add `permission_context` parameter to `AgentLoop.run()`**
- [ ] **Step 2: Check permissions before each tool execution in the loop**
- [ ] **Step 3: Run full test suite**
- [ ] **Step 4: Commit**

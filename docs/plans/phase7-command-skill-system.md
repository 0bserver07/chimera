# Phase 7: Command & Skill System — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add slash command registry, skill loading from `.chimera/skills/` markdown files, bundled skill registration, and a SkillTool bridge so the model can invoke commands.

**Architecture:** `CommandRegistry` centralizes all commands. `SkillLoader` reads markdown+YAML from disk. `SkillTool` lets the model invoke skills. `SlashCommandProcessor` handles `/command` input.

**Tech Stack:** Python 3.11+, PyYAML, dataclasses

**Spec:** `research/specs/phase7-command-skill-system.md`

**Depends on:** Phase 1 (loop), Phase 6 (hooks — skills can register hooks)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `chimera/commands/types.py` | CREATE | `CommandType`, `PromptCommand`, `LocalCommand`, `LocalUICommand`, `CommandBase` |
| `chimera/commands/registry.py` | CREATE | `CommandRegistry` |
| `chimera/commands/processor.py` | CREATE | `SlashCommandProcessor` |
| `chimera/commands/builtins.py` | CREATE | Built-in command implementations (help, clear, compact, cost, etc.) |
| `chimera/skills/definition.py` | CREATE | `SkillDefinition` |
| `chimera/skills/loader.py` | CREATE | `SkillLoader` |
| `chimera/skills/bundled.py` | CREATE | `register_bundled_skill()`, `get_bundled_skills()` |
| `chimera/tools/skill_tool.py` | CREATE | `SkillTool` |
| `tests/commands/test_types.py` | CREATE | |
| `tests/commands/test_registry.py` | CREATE | |
| `tests/commands/test_processor.py` | CREATE | |
| `tests/skills/test_definition.py` | CREATE | |
| `tests/skills/test_loader.py` | CREATE | |
| `tests/tools/test_skill_tool.py` | CREATE | |

---

### Task 1: Command Types

- [ ] **Step 1: Write tests for CommandType enum, PromptCommand, LocalCommand**
- [ ] **Step 2-5: Implement types.py, test, commit**

---

### Task 2: CommandRegistry

- [ ] **Step 1: Write tests**

```python
# tests/commands/test_registry.py
from chimera.commands.registry import CommandRegistry
from chimera.commands.types import LocalCommand

def test_register_and_find():
    registry = CommandRegistry()
    cmd = LocalCommand(name="help", description="Show help", handler=lambda a: "help text")
    registry.register(cmd)
    assert registry.find("help") is not None

def test_find_by_alias():
    registry = CommandRegistry()
    cmd = LocalCommand(name="help", description="Show help", aliases=["h", "?"], handler=lambda a: "help")
    registry.register(cmd)
    assert registry.find("h") is cmd
    assert registry.find("?") is cmd

def test_list_excludes_hidden():
    registry = CommandRegistry()
    cmd1 = LocalCommand(name="help", description="Show help", handler=lambda a: "")
    cmd2 = LocalCommand(name="debug", description="Debug", is_hidden=True, handler=lambda a: "")
    registry.register(cmd1)
    registry.register(cmd2)
    visible = registry.list_commands()
    assert any(c.name == "help" for c in visible)
    assert not any(c.name == "debug" for c in visible)

def test_get_model_invocable():
    registry = CommandRegistry()
    cmd1 = PromptCommand(name="review", description="Code review", source="skills", get_prompt=lambda: "review")
    cmd2 = PromptCommand(name="internal", description="Internal", source="builtin", get_prompt=lambda: "")
    registry.register(cmd1)
    registry.register(cmd2)
    invocable = registry.get_model_invocable()
    assert any(c.name == "review" for c in invocable)
    assert not any(c.name == "internal" for c in invocable)  # builtin excluded
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 3: SkillDefinition

- [ ] **Step 1: Write tests**

```python
# tests/skills/test_definition.py
from chimera.skills.definition import SkillDefinition

def test_expand_arguments():
    skill = SkillDefinition(name="greet", description="greet", prompt_content="Hello $ARGUMENTS")
    result = skill._expand_prompt({"args": "world"})
    assert "world" in result

def test_to_command():
    skill = SkillDefinition(name="review", description="Code review", prompt_content="Review this code")
    cmd = skill.to_command()
    assert cmd.name == "review"
    assert cmd.type.value == "prompt"
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 4: SkillLoader

- [ ] **Step 1: Write tests**

```python
# tests/skills/test_loader.py
import pytest, tempfile
from pathlib import Path
from chimera.skills.loader import SkillLoader

@pytest.mark.asyncio
async def test_loads_skill_from_markdown():
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / ".chimera" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "review.md").write_text(
            "---\nname: review\ndescription: Code review\nallowedTools:\n  - read_file\n---\nReview the code carefully."
        )
        loader = SkillLoader([Path(tmpdir)])
        skills = await loader.load_all()
        assert len(skills) == 1
        assert skills[0].name == "review"
        assert skills[0].allowed_tools == ["read_file"]
        assert "Review the code" in skills[0].prompt_content
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 5: Bundled Skills

- [ ] **Step 1: Write tests for register/get/clear**
- [ ] **Step 2-5: Implement, test, commit**

---

### Task 6: SkillTool

- [ ] **Step 1: Write tests**

```python
# tests/tools/test_skill_tool.py
import pytest
from chimera.tools.skill_tool import SkillTool
from chimera.commands.registry import CommandRegistry
from chimera.commands.types import PromptCommand

@pytest.mark.asyncio
async def test_skill_tool_invokes_inline():
    registry = CommandRegistry()
    cmd = PromptCommand(name="greet", description="Greet", source="skills",
                        context="inline", get_prompt=lambda args=None: "Say hello")
    registry.register(cmd)
    tool = SkillTool(registry)
    result = await tool.async_execute({"skill": "greet"}, None)
    assert result.success
    assert "greet" in result.output.lower() or result.metadata.get("inline_prompt")

@pytest.mark.asyncio
async def test_skill_tool_unknown_skill():
    registry = CommandRegistry()
    tool = SkillTool(registry)
    result = await tool.async_execute({"skill": "nonexistent"}, None)
    assert result.error is not None or "Unknown" in result.output
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 7: SlashCommandProcessor

- [ ] **Step 1: Write tests**

```python
# tests/commands/test_processor.py
import pytest
from chimera.commands.processor import SlashCommandProcessor
from chimera.commands.registry import CommandRegistry
from chimera.commands.types import LocalCommand

@pytest.mark.asyncio
async def test_processes_slash_command():
    registry = CommandRegistry()
    registry.register(LocalCommand(name="help", description="help", handler=lambda a: "Help text"))
    processor = SlashCommandProcessor(registry)
    was_cmd, output = await processor.process("/help")
    assert was_cmd is True
    assert output == "Help text"

@pytest.mark.asyncio
async def test_non_slash_passes_through():
    registry = CommandRegistry()
    processor = SlashCommandProcessor(registry)
    was_cmd, output = await processor.process("hello world")
    assert was_cmd is False
```

- [ ] **Step 2-5: Implement, test, commit**

---

### Task 8: Built-in Commands

- [ ] **Step 1: Implement help, clear, compact, cost, exit**
- [ ] **Step 2: Write tests for each**
- [ ] **Step 3: Commit**

---

### Task 9: Integration — Load Commands at Startup

- [ ] **Step 1: `CommandRegistry.load_all()` aggregates builtin + bundled + disk + plugin commands**
- [ ] **Step 2: Wire into Agent initialization**
- [ ] **Step 3: Run full test suite**
- [ ] **Step 4: Commit**

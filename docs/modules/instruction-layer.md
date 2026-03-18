# Instruction Layer

`chimera.core.instruction` builds system prompts from composable layers
instead of a single monolithic string. Each layer can be independently
added, enabled, disabled, or reordered by priority. Inspired by Codex's
personality + instruction + project doc system.

## Key Classes

| Class | Description |
|-------|-------------|
| `InstructionLayer` | Composable prompt builder with priority-ordered layers |
| `Layer` | A single layer with `name`, `content`, `priority`, and `enabled` |

## Quick Start

```python
from chimera.core.instruction import InstructionLayer

il = InstructionLayer()
il.add("base", "You are a coding assistant.", priority=100)
il.add("personality", "Be concise. No fluff.", priority=90)
il.add("project", "This project uses Python 3.12 and pytest.", priority=50)

prompt_text = il.render()
# Layers are joined in priority order (highest first)
```

## Enabling and Disabling Layers

```python
il.disable("personality")   # Temporarily turn off a layer
il.enable("personality")    # Re-enable it
il.remove("project")        # Remove entirely (returns True/False)
```

## Variable Substitution

`render()` supports `{variable}` replacement:

```python
il.add("greeting", "Hello {user_name}, working on {project}.", priority=80)
text = il.render(user_name="Alice", project="chimera")
```

## Loading from Files

```python
il.add_from_file("project", "~/my-project/AGENT.md", priority=50)
il.add_from_directory("~/.chimera/instructions/", priority_start=40)
```

## Presets

```python
# General coding agent with optional project context
il = InstructionLayer.coding_agent(project_context="Uses Flask + PostgreSQL")

# Code reviewer
il = InstructionLayer.reviewer()
```

## Converting to a Prompt

Use `to_prompt()` to get a Chimera `Prompt` object for use with an agent:

```python
prompt = il.to_prompt(user_name="Alice")
```

## Import Reference

```python
from chimera.core.instruction import InstructionLayer, Layer
```

## Related

- [Demonstration Prompt](demonstration-prompt.md) -- few-shot example prompting
- [Agent Config](agents-config.md) -- agent configuration with presets

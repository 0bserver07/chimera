---
title: "chimera.agents"
description: "Reference for chimera.agents — AgentConfig, presets, and agent loaders."
---

`chimera.agents` is the declarative agent layer: define an agent as a
dataclass (or a markdown file with YAML frontmatter), then call
`build()` to wire it up.

For a narrative walk-through, see
[modules/agents-config](/modules/agents-config/). This page is the
canonical export list.

## Top-level exports

```python
from chimera.agents import (
    AgentConfig,
    AgentLoader,
    AgentRegistry,
    FileAgentDef,
    AgentFactory,
)
from chimera.assembly.coding_agent import CodingAgent  # canonical preset entry point
```

| Symbol | Purpose |
|---|---|
| `AgentConfig` | Dataclass describing tools, loop, permissions, model, prompt. `AgentConfig.from_markdown(path)` parses YAML frontmatter; `.build(provider)` returns a wired `Agent`. |
| `AgentLoader` | Discovers agents on disk with priority order **project > user > built-in**. |
| `AgentRegistry` | Name → factory registry. `.register(name, factory)`, `.get(name)`, `.list()`, `.load_directory(path)`. |
| `FileAgentDef` | Parsed markdown agent definition (frontmatter + system prompt body). |
| `AgentFactory` | Callable that constructs an agent from a config + provider/env. |

## Built-in presets (`chimera.agents.presets`)

Five preset agent definitions ship under `chimera/agents/presets/`:

| Preset markdown | Description |
|---|---|
| `build.py` | Build/implement loop with full tool set. |
| `plan.py` | Plan-only loop, read-only tools. |
| `explore.py` | Read-only exploration agent. |
| `general.py` | General-purpose default. |
| `review.py` | Multi-perspective code reviewer. |

Plus a directory of subagent markdown definitions under
`chimera/agents/presets/subagents/`.

The legacy `AgentPreset` class (`chimera.agents.presets.AgentPreset`)
still exists for backwards-compatible tests, but new code should use
`CodingAgent.from_preset(name)` from `chimera.assembly.coding_agent`:

```python
from chimera.assembly.coding_agent import CodingAgent

agent = CodingAgent.from_preset("coding_agent")  # canonical full preset
agent = CodingAgent.from_preset("codex")         # Codex-style
agent = CodingAgent.from_preset("kimi")          # Kimi-style action-first
agent = CodingAgent.from_preset("swebench")      # SWE-bench tuned
agent = CodingAgent.from_preset("minimal")       # minimal tools
agent = CodingAgent.from_preset("explore")       # read-only
```

The deprecated `claude_code` alias maps to `coding_agent` and emits a
`DeprecationWarning`.

## AgentConfig fields

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | (required) | Unique agent id. |
| `description` | `str` | (required) | Human-readable summary. |
| `system_prompt` | `str` | (required) | System prompt text. |
| `tools` | `list[str]` | `[]` | Tool names resolved from the internal tool registry. |
| `permissions` | `str` | `"auto_approve"` | Permission preset name. |
| `loop` | `str` | `"react"` | Loop type name. |
| `max_steps` | `int` | `50` | Max ReAct iterations. |
| `model` | `str \| None` | `None` | Model override (otherwise inherited from provider/env). |

## See also

- [Compose Agents](/compose-agents/) — Pipeline / Ensemble / Supervisor.
- [`chimera.assembly.coding_agent`](/modules/agents-config/) for the
  fully-assembled `CodingAgent` product surface.
- [`chimera.tools`](/reference/tools/) for the resolvable tool names.

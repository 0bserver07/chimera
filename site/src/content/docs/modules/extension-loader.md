---
title: "Extension Loader"
description: "Extension Loader"
---

Chimera supports two extension surfaces that share a common
"directory-of-Markdown" loading idiom:

1. **Custom agents** — `chimera/agents/loader.py` (`AgentLoader`,
   `load_custom_agents`, `FileAgentDef`).
2. **Plugins** — `chimera/plugins/dir_loader.py`
   (`DirectoryPluginLoader`).

Both walk a conventional directory layout, parse YAML-frontmatter
markdown into typed dataclasses, and register the result so the rest
of Chimera (REPL `/agent`, `delegate` tool, MCP servers, hooks) can
discover them. This page covers the directory contracts both loaders
honour.

## Discovery Roots

`AgentLoader` honours three roots, **last-loaded-wins**:

| Priority | Root | Source label |
|----------|------|--------------|
| 1 (lowest) | Built-in | `chimera/builtin_agents/` (packaged) |
| 2 | User | `~/.chimera/agents/*.md` |
| 3 (highest) | Project | `<project_root>/.chimera/agents/*.md` |

```python
from chimera.agents.loader import AgentLoader

loader = AgentLoader(project_root="/path/to/project")
loader.load_all()
agents = loader.list_agents()           # list[FileAgentDef]
reviewer = loader.get("code-reviewer")  # FileAgentDef | None
matches = loader.find_by_trigger("test")
```

A name registered at a higher priority overrides a lower-priority
definition with the same name. `AgentLoader._load_from_dir` swallows
parse errors silently so a malformed in-repo file never breaks
startup; `tests/agents/` exercises a stricter path that surfaces the
errors.

### Subagent profiles (W13-G8)

The four packaged subagents (`planner`, `researcher`, `executor`,
`reviewer`) live in `chimera/agents/presets/subagents/` — separate
from `builtin_agents/` because they're loaded via the older
`AgentConfig` path used by `create_default_registry()` for backward
compatibility:

```python
from chimera.agents.loader import (
    builtin_subagents_dir,
    create_default_registry,
    SUBAGENT_NAMES,        # ("planner", "researcher", "executor", "reviewer")
)
```

See [Agent Spawner](/modules/agent-spawner/) for the contract.

## Markdown Agent Format

Every directory loader expects YAML frontmatter followed by a Markdown
body that becomes the `system_prompt`:

```markdown
---
name: code-reviewer
description: Reviews PRs across four axes
model: claude-sonnet-4-6
tools: [read_file, search, list_files, git, repo_map]
permissions: read_only
loop: react
max_steps: 30
triggers: [review, audit, lint]
skills: [security-review, code-style]
---
You are a careful PR reviewer. Always end with a verdict:
APPROVE / REQUEST_CHANGES / COMMENT.
```

| Frontmatter key | Type | Default | Meaning |
|-----------------|------|---------|---------|
| `name` | str | filename stem | Unique agent identifier |
| `description` | str | `""` | Short blurb shown by `/agent list` |
| `model` | str | None | Model override |
| `tools` | list[str] | `[]` | Tool names resolved through `_TOOL_REGISTRY` |
| `permissions` | str | `"auto_approve"` | Preset name from `_PERMISSION_REGISTRY` |
| `loop` | str | `"react"` | Loop name from `_LOOP_REGISTRY` |
| `max_iterations` / `max_steps` | int | `50` | Max loop turns |
| `triggers` | list[str] | `[]` | Keywords for `find_by_trigger` matching |
| `skills` | list[str] | `[]` | Skill names injected into `system_prompt` |

`FileAgentDef.from_file(path)` returns a typed dataclass; the older
`AgentConfig.from_markdown(path)` returns a buildable `AgentConfig`
(documented in [Agents & Config](/modules/agents-config/)). The two
formats overlap; new code should prefer `FileAgentDef`.

## Plugin Directory Layout

`DirectoryPluginLoader` reads:

```
my-plugin/
├── plugin.json            # manifest (name, version, description, author)
├── agents/
│   └── code-reviewer.md   # FileAgentDef
├── hooks/
│   └── hooks.json         # list[Hook]
└── .mcp.json              # {"servers": {"name": MCPServerConfig}}
```

```python
from chimera.plugins import DirectoryPluginLoader, ComponentRegistry

plugin = DirectoryPluginLoader().load("/path/to/my-plugin")
plugin.activate(ComponentRegistry())
```

The activate() call walks each subdirectory and registers what it
finds via `PluginExtensionRegistry`:

| File | Registered via |
|------|---------------|
| `agents/*.md` | `PluginExtensionRegistry.register_agent` |
| `.mcp.json` | `PluginExtensionRegistry.register_mcp_server` |
| `hooks/hooks.json` | `PluginExtensionRegistry.register_hook` |

## Skills

`chimera.skills.discovery.discover_skills(roots)` walks `SKILL.md`
files with YAML frontmatter and returns ready-to-inject
`SkillDefinition` instances. Skills are not loaded by the directory
plugin loader directly; they're discovered at REPL startup or
explicitly registered via the agent registry's `skills:` frontmatter
key.

See [Skill Discovery](/modules/skill-discovery/) for the discovery
roots and `SKILL.md` format.

## Bulk loading at runtime

For one-off use without an `AgentLoader`:

```python
from chimera.agents.loader import (
    create_default_registry,
    load_custom_agents,
)

registry = create_default_registry()
loaded_names = load_custom_agents(registry, "./.chimera/agents")
print(loaded_names)  # e.g. ["my-reviewer", "my-tester"]
```

`create_default_registry()` pre-loads the five long-lived presets +
the four subagent profiles; `load_custom_agents` adds your project's
overrides on top.

## Related

- [Agent Spawner](/modules/agent-spawner/) — runs sub-agents discovered here
- [Agents & Config](/modules/agents-config/) — `AgentConfig` + registries
- [Plugins](/modules/plugins/) — `BasePlugin`, `PluginManager`, marketplace
- [Skill Discovery](/modules/skill-discovery/) — `SKILL.md` discovery + injection

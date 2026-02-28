# Agents & Config

`chimera.agents` provides a declarative system for defining and building
agents.  An `AgentConfig` dataclass describes what an agent needs -- tools,
loop, permissions, model -- and the `build()` method resolves everything by
name through internal registries.

## AgentConfig dataclass

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | (required) | Unique agent identifier |
| `description` | `str` | (required) | Human-readable summary |
| `system_prompt` | `str` | (required) | System prompt text |
| `tools` | `list[str]` | `[]` | Tool names resolved from `_TOOL_REGISTRY` |
| `permissions` | `str` | `"auto_approve"` | Permission preset name |
| `loop` | `str` | `"react"` | Loop type name |
| `max_steps` | `int` | `50` | Max ReAct iterations |
| `model` | `str \| None` | `None` | Model override |

### from_markdown(path)

Parses a `.md` file with YAML frontmatter.  The body after the second `---`
delimiter becomes the `system_prompt`.

```markdown
---
name: my-agent
description: A custom agent
tools: [read_file, bash, search]
permissions: interactive
loop: react
max_steps: 30
---
You are a custom agent that helps with file management.
Always confirm before deleting files.
```

### build(provider, env)

Constructs a fully wired `Agent` by resolving all names through the registries:

```python
from chimera.agents import AgentConfig
from chimera.providers.anthropic import AnthropicProvider

config = AgentConfig.from_markdown("agents/my-agent.md")
agent = config.build(AnthropicProvider())
result = agent.run("Refactor the utils module.")
```

## Registries

Three internal dictionaries map string names to import paths:

### Tool Registry (`_TOOL_REGISTRY`)

| Name | Import path |
|------|-------------|
| `read_file` | `chimera.tools:read_file` |
| `write_file` | `chimera.tools:write_file` |
| `edit_file` | `chimera.tools:edit_file` |
| `bash` | `chimera.tools:bash` |
| `search` | `chimera.tools:search` |
| `list_files` | `chimera.tools:list_files` |
| `test` | `chimera.tools:test` |
| `web_fetch` | `chimera.tools:web_fetch` |
| `git` | `chimera.tools:git` |
| `replace_in_file` | `chimera.tools:replace_in_file` |
| `verify` | `chimera.tools:verify` |
| `repo_map` | `chimera.tools.repo_map:RepoMapTool` |

### Loop Registry (`_LOOP_REGISTRY`)

| Name | Import path |
|------|-------------|
| `react` | `chimera.core.loop:ReAct` |
| `plan_execute` | `chimera.core.loops.plan_execute:PlanAndExecute` |
| `reflexion` | `chimera.core.loops.reflexion:Reflexion` |

### Permission Registry (`_PERMISSION_REGISTRY`)

| Name | Import path |
|------|-------------|
| `auto_approve` | `chimera.permissions.presets:AutoApprove` |
| `always_deny` | `chimera.permissions.presets:AlwaysDeny` |
| `read_only` | `chimera.permissions.presets:ReadOnly` |
| `interactive` | `chimera.permissions.presets:Interactive` |

## AgentRegistry

An in-memory registry for looking up `AgentConfig` instances by name.

| Method | Description |
|--------|-------------|
| `register(config)` | Add or overwrite a config keyed by `config.name` |
| `get(name)` | Return the config or `None` |
| `list()` | Return all registered names in insertion order |
| `load_directory(path)` | Bulk-load every `.md` file in a directory |

```python
from chimera.agents import AgentRegistry

registry = AgentRegistry()
registry.load_directory("./agents/")

config = registry.get("my-agent")
agent = config.build(provider)
```

## Preset agents

Five factory functions create pre-configured agents.  Each wraps an
`AgentConfig` and accepts `**overrides` to customise fields.

| Factory | Tools | Permissions | Loop |
|---------|-------|-------------|------|
| `BuildAgent` | read, write, edit, bash, search, list, test | `interactive` | `react` (100 steps) |
| `PlanAgent` | read, search, list, repo_map | `read_only` | `plan_execute` |
| `ExploreAgent` | read, search, list, repo_map | `read_only` | `react` |
| `GeneralAgent` | read, write, edit, bash, search, list, test, git | `auto_approve` | `react` |
| `ReviewAgent` | read, search, list, git, repo_map | `read_only` | `react` |

```python
from chimera.agents import BuildAgent, ExploreAgent
from chimera.providers.anthropic import AnthropicProvider

provider = AnthropicProvider()

# Default build agent
agent = BuildAgent(provider)

# Explore agent with custom step limit
agent = ExploreAgent(provider, max_steps=20)
```

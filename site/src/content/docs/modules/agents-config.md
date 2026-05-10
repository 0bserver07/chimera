---
title: "Agents & Config"
description: "Agents & Config"
---

`chimera.agents` provides a declarative system for defining and building
agents. An `AgentConfig` dataclass describes what an agent needs —
tools, loop, permissions, model — and the `build()` method resolves
everything by name through internal registries.

> **Heads-up.** For full coding-agent assemblies (permissions, hooks,
> transcripts, compaction, streaming wired together) prefer
> `chimera.assembly.coding_agent.CodingAgent.from_preset(...)`. This
> page covers the lower-level `AgentConfig` building block — useful
> when you want to spell out the exact tool / loop / permission set
> yourself or load it from Markdown frontmatter. See
> [Agent Presets](/modules/agent-presets/) for the high-level path.

## AgentConfig dataclass

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | (required) | Unique agent identifier |
| `description` | `str` | (required) | Human-readable summary |
| `system_prompt` | `str` | (required) | System prompt text |
| `tools` | `list[str]` | `[]` | Tool names resolved from `_TOOL_REGISTRY` |
| `permissions` | `str` | `"auto_approve"` | Permission preset name |
| `loop` | `str` | `"react"` | Loop type name |
| `max_steps` | `int` | `50` | Max loop iterations |
| `model` | `str \| None` | `None` | Model override |

### from_markdown(path)

Parses a `.md` file with YAML frontmatter. The body after the second
`---` delimiter becomes the `system_prompt`.

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

Constructs a fully wired `Agent` by resolving all names through the
registries:

```python
from chimera.agents import AgentConfig
from chimera.providers import create_provider

config = AgentConfig.from_markdown("agents/my-agent.md")
agent = config.build(create_provider())
result = agent.run("Refactor the utils module.")
```

## Registries

Three internal dictionaries map string names to import paths.

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

For the full agent tool set (23 tools including `apply_patch`,
`write_guard`, `notebook_edit`, `enter_worktree` / `exit_worktree`,
`cron_create` / `cron_list` / `cron_delete`) use
`chimera.AGENT_TOOLS` directly instead of resolving by name. See
[AGENT_TOOLS](/modules/agent-tools/).

### Loop Registry (`_LOOP_REGISTRY`)

| Name | Import path |
|------|-------------|
| `react` | `chimera.core.loop:ReAct` |
| `plan_execute` | `chimera.core.loops.plan_execute:PlanAndExecute` |
| `reflexion` | `chimera.core.loops.reflexion:Reflexion` |

The other loop variants (`AgentLoop`, `RetryLoop`, `LintFeedbackLoop`,
`PlanActLoop`, `TreeOfThought`, `AutonomousLoop`) are not currently
spellable from frontmatter; instantiate them directly. See
[Loops](/modules/loops/).

### Permission Registry (`_PERMISSION_REGISTRY`)

| Name | Import path |
|------|-------------|
| `auto_approve` | `chimera.permissions.presets:AutoApprove` |
| `always_deny` | `chimera.permissions.presets:AlwaysDeny` |
| `read_only` | `chimera.permissions.presets:ReadOnly` |
| `interactive` | `chimera.permissions.presets:Interactive` |

For the standard 5-mode `--permission-mode` surface
(`read-only` / `suggest` / `auto` / `yolo` / `strict`) use
`chimera.permissions.modes.policy_for_mode(...)` instead. See
[Permissions](/modules/permissions/).

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

## Default Registry (`create_default_registry`)

`chimera.agents.loader.create_default_registry()` returns a registry
pre-loaded with **9** agents:

- 5 long-lived presets: `build`, `explore`, `general`, `plan`, `review`
- 4 packaged subagent profiles (W13-G8): `planner`, `researcher`,
  `executor`, `reviewer`

This is the registry consumed by every CLI's `/agent` slash command
and the `--agent <name>` flag. See [Agent Spawner](/modules/agent-spawner/)
for the subagent contract.

## Preset agents

Five factory functions create pre-configured agents. Each wraps an
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
from chimera.providers import create_provider

provider = create_provider()

# Default build agent
agent = BuildAgent(provider)

# Explore agent with custom step limit
agent = ExploreAgent(provider, max_steps=20)
```

## Related

- [Agent Presets](/modules/agent-presets/) — high-level `CodingAgent.from_preset(...)`
- [Agent Spawner](/modules/agent-spawner/) — sub-agent dispatch + 4 default subagents
- [Extension Loader](/modules/extension-loader/) — `~/.chimera/agents/` discovery
- [AGENT_TOOLS](/modules/agent-tools/) — the 23-tool default group

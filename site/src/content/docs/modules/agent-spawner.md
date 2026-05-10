---
title: "Agent Spawner"
description: "Agent Spawner"
---

`chimera.core.agent_spawner.AgentSpawner` creates and runs **sub-agents**
from `AgentDefinition` instances. It builds an isolated `AgentContext`,
resolves the tool set, drives an `AgentLoop`, and yields events back to
the caller — synchronously (foreground) or as a background asyncio task.

The spawner is the runtime behind the `delegate` tool, the `/agent` REPL
command, the per-CLI `--agent <name>` flag, and the Supervisor /
Pipeline composition primitives.

## Quick Start

```python
from chimera.core.agent_spawner import AgentSpawner
from chimera.core.builtin_agents import BUILTIN_AGENTS
from chimera.core.task_manager import TaskManager
from chimera.core.agent_context import AgentContext

spawner = AgentSpawner(
    provider=provider,
    available_tools=list(chimera.AGENT_TOOLS),
    task_manager=TaskManager(),
)

parent_ctx = AgentContext(...)
async for event in spawner.spawn(
    BUILTIN_AGENTS["explore"],
    prompt="Map the auth flow",
    parent_context=parent_ctx,
):
    print(event)
```

## Default Subagent Profiles (W13-G8)

`create_default_registry()` ships with **four** packaged subagent
profiles in addition to the five long-lived presets. These are the
ecosystem-parity subagents the orchestrator can dispatch to without
any per-project configuration:

| Subagent | Tools | Permissions | Loop | Role |
|----------|-------|-------------|------|------|
| `planner` | `read_file`, `search`, `list_files`, `repo_map` | `read_only` | `plan_execute` | Produces step-by-step plans without executing. Always finishes with an explicit approval prompt. |
| `researcher` | `read_file`, `search`, `list_files`, `repo_map`, `web_fetch` | `read_only` | `react` | Read-only + web; demands citations (`path/to/file.py:lineno` or URL) for every claim. |
| `executor` | full 11-tool set (read/write/edit/bash/git/test/…) | `auto_approve` | `react` | Carries out a pre-agreed plan. Forbids unilateral scope expansion. |
| `reviewer` | `read_file`, `search`, `list_files`, `git`, `repo_map` | `read_only` | `react` | Three-state verdict (APPROVE / REQUEST_CHANGES / COMMENT) across four axes (correctness / tests / readability / risk). |

Each profile is a Markdown file under `chimera/agents/presets/subagents/`
(e.g. `planner.md`) loaded via `AgentConfig.from_markdown`. Project /
user overrides loaded via `load_custom_agents(...)` cleanly clobber the
bundled profile because subagents register *after* presets.

The "no edit/write/bash" contract is enforced two ways: (a) the
`tools:` list literally omits those tool names, so the resolved agent
won't have those tools to call; (b) the system prompt explicitly tells
the model "do not call edit / write / bash tools". Belt and braces.

```python
from chimera.agents.loader import (
    SUBAGENT_NAMES,        # ("planner", "researcher", "executor", "reviewer")
    create_default_registry,
    builtin_subagents_dir,
)

registry = create_default_registry()
config = registry.get("planner")
agent = config.build(provider)
```

The CLI surface:

- `chimera code` REPL → `/agent planner "..."`
- `chimera mink --agent reviewer ...`
- `chimera ferret agents list`
- `chimera otter agents show planner`

(All seven codename CLIs route through the same default registry, so
the four subagents are reachable without per-CLI wiring.)

## Built-in (in-process) AgentDefinitions

`chimera.core.builtin_agents.BUILTIN_AGENTS` ships three lightweight
`AgentDefinition` instances usable directly with `AgentSpawner` — no
markdown round-trip:

| Name | Tools | Purpose |
|------|-------|---------|
| `general-purpose` | All available | Versatile coding agent for any task |
| `explore` | `read_file`, `glob`, `grep`, `list_files` | Read-only codebase exploration |
| `plan` | reads + `web_fetch`, `web_search` | Produces detailed implementation plans |

These are imported with the spawner directly; the four packaged
subagent markdowns are imported through the `AgentRegistry`.

## API

### AgentSpawner

```python
class AgentSpawner:
    def __init__(
        self,
        *,
        provider: Provider,
        available_tools: list[BaseTool],
        task_manager: TaskManager,
        hook_emitter: HookEmitter | None = None,
        auto_bg_config: AutoBackgroundConfig | None = None,
    ) -> None: ...

    async def spawn(
        self,
        definition: AgentDefinition,
        prompt: str,
        parent_context: AgentContext,
        *,
        model_override: str | None = None,
        run_in_background: bool = False,
        isolation: IsolationLevel = IsolationLevel.FULL,
        share_abort: bool = False,
    ) -> AsyncGenerator[LoopEvent, None]: ...
```

| Argument | Purpose |
|----------|---------|
| `definition` | `AgentDefinition` describing tools, prompt, model |
| `prompt` | User prompt for the sub-agent |
| `parent_context` | Caller's context — child inherits / forks |
| `model_override` | Override the definition's model choice |
| `run_in_background` | Launch as an asyncio task; yield a single `system` event |
| `isolation` | `IsolationLevel.FULL` (default) / `SHARED_TOOLS` / `SHARED_HOOKS` |
| `share_abort` | If `True`, child cancels when parent does |

### Lifecycle hooks fired

`AgentSpawner` fires two lifecycle hook events automatically:

- `SubagentStart` — before the loop begins
- `SubagentStop` — after the loop ends (foreground or background)
- `TeammateIdle` — additionally fired after a backgrounded sub-agent
  finishes, so coordinators can pick up the next piece of work

These thread through the `hook_emitter` argument (or a default-
constructed one) and are visible to every registered `HookMatcher`.

### Auto-backgrounding

If `auto_bg_config` is supplied, the spawner monitors elapsed
wall-clock time and converts a long-running foreground spawn into a
background task once the configured threshold is exceeded. A single
`auto-backgrounded` `system` event is yielded; the rest of the run
continues in the background and the caller's `async for` loop unblocks.

## AgentDefinition

```python
from chimera.core.agent_definition import AgentDefinition

definition = AgentDefinition(
    name="my-agent",
    description="A custom agent",
    model=None,                              # use provider default
    tools=["read_file", "search", "bash"],   # None = all available
    system_prompt="You are a careful auditor...",
)
```

The spawner resolves the `tools` list by name from `available_tools`;
unknown names are silently dropped. `tools=None` selects all tools.

## Related

- [Agents & Config](/modules/agents-config/) — `AgentConfig`, registries, presets
- [Agent Presets](/modules/agent-presets/) — the five long-lived presets
- [Extension Loader](/modules/extension-loader/) — discovering markdown agents
- [LoopConfig](/modules/loop-config/) — cross-cutting loop configuration

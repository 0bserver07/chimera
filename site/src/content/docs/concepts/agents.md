---
title: "Agents"
description: "Agents"
---

An **Agent** is the central orchestrator in Chimera. It wires together four components -- a Provider (the LLM), a set of Tools, a reasoning Loop, and a system Prompt -- and exposes a single `run(task, env)` method that returns an `AgentResult`.

## The Agent Equation

```
Agent = Provider + Tools + Loop + Prompt
```

The `Agent` class lives in `chimera.core.agent` and is deliberately minimal (under 50 lines). All complexity is pushed to the composable pieces it holds.

## Agent Lifecycle

### 1. Construction

```python
from chimera.core.agent import Agent
from chimera.providers.factory import create_provider
from chimera.core.loop import ReAct
from chimera.core.prompt import Prompt
from chimera.tools import ReadFileTool, WriteFileTool, BashTool

agent = Agent(
    provider=create_provider(model="claude-sonnet-4-20250514"),
    tools=[ReadFileTool(), WriteFileTool(), BashTool()],
    loop=ReAct(max_steps=50),
    prompt=Prompt.from_string("You are a helpful coding agent."),
    name="my-agent",
)
```

All arguments except `provider` have defaults:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tools` | `[]` | List of `BaseTool` instances |
| `loop` | `ReAct()` | Reasoning loop (50 steps max) |
| `prompt` | `"You are a helpful coding agent."` | System prompt template |
| `name` | `None` | Optional human-readable name |

### 2. Running

```python
result = agent.run("Implement a fibonacci function in fib.py", env)
```

Internally, `run()` does three things:

1. **Renders the prompt** -- calls `self.prompt.render(tools=...)` to produce the system message, including a listing of available tools.
2. **Creates a Context** -- a fresh `Context(system=system)` that manages conversation history.
3. **Delegates to the loop** -- calls `self.loop.run(provider, tools, context, env)` and returns the `AgentResult`.

### 3. AgentResult

The return value is a dataclass with everything you need:

```python
@dataclass
class AgentResult:
    output: str           # Final text response
    steps: int            # Number of reasoning steps
    tool_calls_total: int # Total tool invocations
    cost: float           # Estimated USD cost
    success: bool         # Whether the agent completed successfully
    error: str | None     # Error message if success=False
```

## Prompt and Context

### Prompt

`Prompt` is a lightweight template engine with `{{variable}}` substitution (no Jinja2 dependency). It supports two constructors:

```python
# From a string
prompt = Prompt.from_string("You are a {{role}} agent.")

# From a file
prompt = Prompt.from_file("prompts/system.txt")
```

When rendered, the prompt automatically appends a list of available tool names.

### Context

`Context` manages the conversation history for a single agent run. It holds a system message and an ordered list of `Message` objects (user, assistant, tool).

```python
context = Context(system="You are helpful.")
context.add(Message.user("Write a function."))
context.add(Message.assistant("Sure, here is the function..."))
messages = context.to_messages()  # Includes system message
```

## Three API Tiers

Chimera offers three levels of abstraction depending on how much control you need.

### Tier 1: One-liner via `synthesize()`

```python
from chimera import synthesize

result = synthesize(
    "Build a REST API with FastAPI",
    tests="tests/",
    model="claude-sonnet-4-20250514",
)
```

This wires up an Agent, Provider, Environment, Trainer, and Strategy automatically. Good for quick prototyping.

### Tier 2: Configured Agent

```python
from chimera.core.agent import Agent
from chimera.providers.factory import create_provider
from chimera.core.loop import ReAct
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.local import LocalEnvironment

provider = create_provider(model="claude-sonnet-4-20250514")
agent = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),
    loop=ReAct(max_steps=100),
)

with LocalEnvironment(workdir="./output") as env:
    result = agent.run("Implement a calculator module", env)
    print(f"Steps: {result.steps}, Cost: ${result.cost:.4f}")
```

### Tier 3: Subclass Agent

For advanced use cases, subclass `Agent` and override `run()`:

```python
class MyAgent(Agent):
    def run(self, task, env):
        # Custom pre-processing
        task = f"[IMPORTANT] {task}\nAlways write tests first."
        result = super().run(task, env)
        # Custom post-processing
        if not result.success:
            result = super().run(f"Fix the error: {result.error}", env)
        return result
```

:::tip[Default Tools]
`DEFAULT_TOOLS` is a `ToolGroup` containing `ReadFileTool`, `WriteFileTool`, `BashTool`, and `ImageReadTool` -- the minimum set for most coding tasks. For interactive sessions, use `AGENT_TOOLS` (a 23-tool preset including edit, search, git, think, todo, apply_patch, write_guard, notebook_edit, worktree, and cron tools). Import both from `chimera.core.tool_group`.
:::## CodingAgent: the canonical assembled stack

For production-style coding-agent use, prefer `CodingAgent` over building an
`Agent` by hand. `CodingAgent` is the fully-assembled product class that
wires every phase of Chimera (loop, sub-agents, sessions, permissions,
prompt, hooks, commands, production infrastructure, snapshot/format/patch)
into a single buildable agent. The `Agent` class above remains the
back-compat-friendly low-level seam that the synthesis pipeline
(`Trainer`, `synthesize()`) builds on.

```python
from chimera.assembly.coding_agent import CodingAgent

# Default full-featured coding agent
agent = CodingAgent(model="glm-5")

# Or with a named preset (passing kwargs through to __init__)
agent = CodingAgent.from_preset("codex", model="gpt-4o")

async for event in agent.run("Fix the bug in auth.py"):
    print(event)
```

### CodingAgent presets

`CodingAgent.from_preset(name, **kwargs)` looks up `name` in
`chimera.assembly.presets.PRESETS` and applies the matching
`AssemblyConfig` (tool set, system prompt, permissions on/off, hooks
on/off, transcripts, content replacement, compaction, streaming, max
turns). Pass any other `CodingAgent` kwarg through (model, project_dir,
provider, permission_callback, tools_override).

| Preset | Tool set | Permissions | Hooks | Transcripts | Compaction | Streaming | Max turns | Description |
|--------|----------|-------------|-------|-------------|------------|-----------|-----------|-------------|
| `coding_agent` | `coding` | on | on | on | on | on | 100 | Full-featured (canonical default) |
| `codex` | `coding` | on | off | on | on | on | 50 | Codex-style code generation |
| `kimi` | `coding` | on | off | on | on | on | 50 | Action-first, KISS, iterate-on-failure |
| `swebench` | `coding` | off | off | off | off | off | 30 | SWE-bench-tuned, minimal scaffold, root-cause focus |
| `minimal` | `minimal` | off | off | off | on | on | 20 | Bare-bones agent for tests / smoke checks |
| `explore` | `explore` | off | off | off | on | on | 30 | Read-only exploration agent |
| `claude_code` | `coding` | on | on | on | on | on | 100 | Deprecated alias for `coding_agent` (raises `DeprecationWarning`; maps to the canonical preset) |

:::caution[`claude_code` preset is deprecated]
The legacy `claude_code` preset is a structural alias for `coding_agent`
and will be removed in a future release. Migrate to `coding_agent` —
`CodingAgent` emits a `DeprecationWarning` when the legacy key is used.
:::

### The 7-CLI architecture

`CodingAgent` is the shared library every coding-agent CLI inherits.
Chimera ships seven of them, each composed from the same nine-phase
core but with different per-CLI defaults (step budget, slash-command
surface, permission preset, transport):

| CLI | Alias | Posture |
|-----|-------|---------|
| `chimera mink` | `tui` | TUI-first interactive coding agent |
| `chimera otter` | `multi` | Server-first, multi-client (HTTP+SSE, ACP serve) |
| `chimera ferret` | `sandbox` | IDE-flagship, sandbox × approval composition |
| `chimera weasel` | `mini` | Minimal harness, four modes, sub-agents off by design |
| `chimera shrew` | `tiny` | Tuned for small local models |
| `chimera stoat` | `shell` | Shell-mode toggle, Kimi-tuned defaults |
| `chimera badger` | `strict` | Harness-rewrite posture, parity tracking |

For the per-CLI quickstart, slash-command surface, and parity row, see
each CLI's documentation:

- [Mink quickstart](/chimera/mink/quickstart/) — TUI-first
- [Otter quickstart](/chimera/otter/quickstart/) — server-first / multi-client
- [Ferret quickstart](/chimera/ferret/quickstart/) — IDE-flagship, sandbox-first
- [Weasel quickstart](/chimera/weasel/quickstart/) — minimal harness
- [Shrew quickstart](/chimera/shrew/quickstart/) — small local models
- [Stoat quickstart](/chimera/stoat/quickstart/) — shell-mode toggle
- [Badger quickstart](/chimera/badger/quickstart/) — harness discipline

The `chimera which` and `chimera agents` discovery commands surface the
full list at the terminal — see the [Quickstart](/chimera/quickstart/)
for the 7-CLI tour.

## Related concepts

- [Sub-agent profiles](/chimera/concepts/subagent-profiles/) — the four built-in profiles (`planner`, `researcher`, `executor`, `reviewer`) the dispatch router routes to.
- [Permission modes](/chimera/concepts/permission-modes/) — the five-mode approval surface (`read-only` / `suggest` / `auto` / `yolo` / `strict`) wired into every CLI.
- [Hook events](/chimera/concepts/hook-events/) — the 27 lifecycle events agent runs emit, with payload schemas.
- [File undo](/chimera/concepts/file-undo/) — otter's per-session content-addressed snapshot store powering `/undo` and `/redo`.

## API Reference

- `chimera.core.agent.Agent` -- main agent class (low-level seam)
- `chimera.core.context.Context` -- conversation history manager
- `chimera.core.prompt.Prompt` -- system prompt template
- `chimera.types.AgentResult` -- result dataclass
- `chimera.synthesize.synthesize` -- top-level one-liner
- `chimera.assembly.coding_agent.CodingAgent` -- fully-assembled production agent
- `chimera.assembly.presets.PRESETS` -- registered `AssemblyConfig` presets
- `chimera.assembly.presets.AssemblyConfig` -- preset config dataclass

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

!!! tip "Default Tools"
    `DEFAULT_TOOLS` is a `ToolGroup` containing `ReadFileTool`, `WriteFileTool`, `BashTool`, and `ImageReadTool` -- the minimum set for most coding tasks. For interactive sessions, use `AGENT_TOOLS` (13 tools including edit, search, git, think, todo, and more). Import both from `chimera.core.tool_group`.

## API Reference

- `chimera.core.agent.Agent` -- main agent class
- `chimera.core.context.Context` -- conversation history manager
- `chimera.core.prompt.Prompt` -- system prompt template
- `chimera.types.AgentResult` -- result dataclass
- `chimera.synthesize.synthesize` -- top-level one-liner

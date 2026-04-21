---
title: "Build a Coding Agent"
description: "Build a Coding Agent"
---

This guide walks you through building an interactive coding agent from scratch.
By the end you will have an agent that reads files, runs shell commands, holds
multi-turn conversations, detects loops, and respects permission guardrails.

---

## 1. Setup

Install chimera and export your API key:

```bash
pip install chimera-ai[anthropic]
export ANTHROPIC_API_KEY="sk-..."
```

Create a provider -- the bridge between your agent and the language model:

```python
from chimera import create_provider

provider = create_provider(model="claude-sonnet-4-20250514")
```

`create_provider` infers the backend from the model name.  The `model`
parameter is optional -- when omitted, it falls back to the `ANTHROPIC_MODEL`
environment variable.  You can also pass `provider_type` explicitly
(e.g. `"openai"`, `"google"`, `"ollama"`).

---

## 2. Basic Agent

An `Agent` wires together four things: a **provider**, a list of **tools**, a
**loop** (reasoning strategy), and a **prompt**.

```python
from chimera import Agent, DEFAULT_TOOLS, Prompt, create_provider

provider = create_provider(model="claude-sonnet-4-20250514")

agent = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),        # read_file, write_file, bash, image_read
    prompt=Prompt.from_string("You are a helpful coding assistant."),
)

result = agent.run("List the Python files in the current directory.", env=None)
print(result.output)
print(f"Steps: {result.steps}, Cost: ${result.cost:.4f}")
```

`DEFAULT_TOOLS` is a `ToolGroup` containing `ReadFileTool`, `WriteFileTool`,
`BashTool`, and `ImageReadTool`.  For interactive sessions with more tools
(edit, search, git, think, todo, etc.), use `AGENT_TOOLS` instead -- a 13-tool
preset that the REPL uses by default.  Pass `env=None` when you do not need a
managed workspace, or supply a `LocalEnvironment("./workspace")` for sandboxed
file access.

---

## 3. Add a LoopConfig

`LoopConfig` injects cross-cutting behaviour into the reasoning loop --
permissions, events, loop detection, and streaming -- without changing the
agent itself.

### Permissions

```python
from chimera import PermissionRuleset, Rule, PermissionAction

permissions = PermissionRuleset(
    rules=[
        Rule(tool_pattern="read_file", action=PermissionAction.ALLOW),
        Rule(tool_pattern="bash", action=PermissionAction.DENY,
             arg_key="command", arg_pattern="rm *"),
        Rule(tool_pattern="*", action=PermissionAction.ASK),
    ],
    default=PermissionAction.ASK,
)
```

### Events

```python
from chimera import EventBus, ToolCallEvent

bus = EventBus()

@bus.on("tool_call")
def log_tool_call(event: ToolCallEvent):
    print(f"  -> {event.tool_name}({event.arguments})")
```

### Loop detection

```python
from chimera.detection.actions import LoopDetector, OnDetect

detector = LoopDetector(on_detect=OnDetect.BREAK, threshold=3)
```

### Wire it all together

```python
from chimera import LoopConfig, ReAct

config = LoopConfig(
    permissions=permissions,
    event_bus=bus,
    detector=detector,
)

loop = ReAct(max_steps=30, config=config)

agent = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),
    loop=loop,
    prompt=Prompt.from_string("You are a helpful coding assistant."),
)
```

---

## 4. Add Sessions

A `Session` wraps an agent and maintains a running conversation context.
Each call to `session.chat()` appends to the same message history.

```python
from chimera import Session, FileStorage

storage = FileStorage("./sessions")
session = Session(agent=agent, storage=storage)

# First turn
r1 = session.chat("Read main.py and summarise it.")
print(r1.output)

# Second turn -- the agent remembers the first turn
r2 = session.chat("Now add type hints to the functions you found.")
print(r2.output)
```

### Save and resume

```python
# Persist the session
session.save()
sid = session.session_id
print(f"Saved session {sid}")

# Later, resume where you left off
resumed = Session.resume(sid, agent=agent, storage=storage)
r3 = resumed.chat("Run the test suite to make sure the type hints are correct.")
print(r3.output)
```

### Fork a session

`session.fork()` creates an independent branch with the same history.  This is
useful for exploring two approaches from the same starting point.

```python
branch = session.fork()
r_branch = branch.chat("Try a different approach: use dataclasses instead.")
```

---

## 5. Full Example

Below is the complete program combining everything from the previous sections.

```python
"""coding_agent.py -- Full interactive coding agent."""

from chimera import (
    Agent,
    DEFAULT_TOOLS,
    EventBus,
    FileStorage,
    LoopConfig,
    PermissionAction,
    PermissionRuleset,
    Prompt,
    ReAct,
    Rule,
    Session,
    ToolCallEvent,
    create_provider,
)
from chimera.detection.actions import LoopDetector, OnDetect

# --- Provider ---
provider = create_provider(model="claude-sonnet-4-20250514")

# --- Permissions ---
permissions = PermissionRuleset(
    rules=[
        Rule(tool_pattern="read_file",  action=PermissionAction.ALLOW),
        Rule(tool_pattern="list_files", action=PermissionAction.ALLOW),
        Rule(tool_pattern="search",     action=PermissionAction.ALLOW),
        Rule(tool_pattern="bash",       action=PermissionAction.DENY,
             arg_key="command", arg_pattern="rm *"),
        Rule(tool_pattern="bash",       action=PermissionAction.DENY,
             arg_key="command", arg_pattern="sudo *"),
        Rule(tool_pattern="*",          action=PermissionAction.ASK),
    ],
)

# --- Events ---
bus = EventBus()

@bus.on("tool_call")
def on_tool(event: ToolCallEvent):
    print(f"  [tool] {event.tool_name}")

# --- Loop detection ---
detector = LoopDetector(on_detect=OnDetect.BREAK, threshold=3)

# --- Loop + Agent ---
config = LoopConfig(
    permissions=permissions,
    event_bus=bus,
    detector=detector,
)
loop = ReAct(max_steps=30, config=config)

agent = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),
    loop=loop,
    prompt=Prompt.from_string(
        "You are a senior software engineer. "
        "Read code, make changes, and run tests."
    ),
)

# --- Session ---
storage = FileStorage("./sessions")
session = Session(agent=agent, storage=storage)

# Chat loop
while True:
    user_input = input("\n> ")
    if user_input.lower() in ("exit", "quit"):
        session.save()
        print(f"Session saved: {session.session_id}")
        break
    result = session.chat(user_input)
    print(result.output)
```

Run it:

```bash
python coding_agent.py
```

---

## Next Steps

- [Add a Custom Tool](/add-custom-tool/) -- give your agent new capabilities.
- [Configure Permissions](/configure-permissions/) -- fine-tune what the agent can do.
- [Compose Agents](/compose-agents/) -- chain multiple agents together.

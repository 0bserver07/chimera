---
title: "Connect External Agents via ACP"
description: "Connect External Agents via ACP"
---

**Goal:** Integrate any external agent (any language, any framework) into Chimera using the Agent Client Protocol (ACP).

---

## Prerequisites

- An external agent that speaks JSON-RPC 2.0 over stdio (e.g. `npx -y claude-code-acp`)
- Chimera installed (`pip install chimera-run`)

---

## Step 1: Configure the session

`ACPSessionConfig` tells Chimera how to spawn the external agent subprocess.

```python
from chimera.acp.types import ACPSessionConfig

config = ACPSessionConfig(
    command=["npx", "-y", "claude-code-acp"],  # command to spawn
    args=["--model", "claude-sonnet-4-20250514"],  # extra CLI args
    env={"ANTHROPIC_API_KEY": "sk-..."},         # environment variables
    working_dir="/path/to/project",              # subprocess cwd
)
```

`notification_drain_delay` (default `0.1`s) controls how long the client waits for trailing notifications after an RPC response.

---

## Step 2: Create a client and manage its lifecycle

```python
from chimera.acp.client import ACPClient

client = ACPClient(config)
client.start()                        # spawns subprocess, calls session/create
response = client.send_message("Write a hello-world script.")
print(response.text)                  # full text from the external agent
print(f"Cost: ${response.cost:.4f}")  # token cost
client.stop()                         # terminates subprocess
```

`send_message()` returns an `ACPResponse` with: `text`, `thoughts`, `tool_calls`, `cost`, `input_tokens`, `output_tokens`.

---

## Step 3: Use as a context manager

For automatic cleanup, use the `with` statement:

```python
with ACPClient(config) as client:
    r1 = client.send_message("List all Python files in this repo.")
    r2 = client.send_message("Now add type hints to the largest one.")
    # subprocess is terminated automatically on exit
```

---

## Step 4: Wrap as a tool

`ExternalAgentTool` adapts an ACP agent into a `BaseTool` that any Chimera agent can call.

```python
from chimera.acp.tool import ExternalAgentTool

tool = ExternalAgentTool(
    config=config,
    agent_name="code_assistant",  # tool name visible to the model
)
```

The tool accepts a `{"task": "..."}` argument. On first invocation it calls `setup()` to start the subprocess; call `cleanup()` when done.

---

## Step 5: Use in composition

Add the external agent tool to a Supervisor so the coordinator can delegate to it alongside native Chimera workers.

```python
from chimera import Agent, Supervisor, Prompt, create_provider

provider = create_provider(model="claude-sonnet-4-20250514")

# Native Chimera worker
reviewer = Agent(
    provider=provider,
    prompt=Prompt.from_string("You are a code reviewer."),
    name="reviewer",
)

# Agent with external tool
coder = Agent(
    provider=provider,
    tools=[tool],  # ExternalAgentTool from Step 4
    prompt=Prompt.from_string("You are a coding agent. Use code_assistant for implementation."),
    name="coder",
)

coordinator = Agent(
    provider=provider,
    prompt=Prompt.from_string(
        "You are a tech lead. Delegate coding to 'coder' and reviews to 'reviewer'."
    ),
)

supervisor = Supervisor(
    coordinator=coordinator,
    workers={"coder": coder, "reviewer": reviewer},
)

result = supervisor.run("Add pagination to the /users endpoint.", env=None)
```

---

## Step 6: Session forking

Fork a session to branch the conversation for parallel exploration:

```python
with ACPClient(config) as client:
    client.send_message("Analyze this codebase.")
    fork_id = client.fork_session()  # returns new session ID
    # Use fork_id to continue a separate conversation branch
```

---

## Step 7: Streaming

Pass an `on_chunk` callback to `send_message()` for real-time output:

```python
with ACPClient(config) as client:
    response = client.send_message(
        "Refactor the database module.",
        on_chunk=lambda chunk: print(chunk, end="", flush=True),
    )
```

Chunks arrive via `agent/messageChunk` notifications interleaved with the RPC response.

---

## Protocol reference

ACP uses JSON-RPC 2.0 over subprocess stdio. The client sends requests; the server may interleave notifications before the final response.

| Method | Description |
|---|---|
| `session/create` | Initialize a session (returns `session_id`) |
| `session/sendMessage` | Send a message, receive response + streamed notifications |
| `session/fork` | Branch the conversation (returns new `session_id`) |

Notification methods during `sendMessage`: `agent/messageChunk`, `agent/thoughtChunk`, `agent/toolCallStart`, `agent/toolCallComplete`, `agent/usageUpdate`.

---

## Complete example

```python
from chimera import Agent, Supervisor, Prompt, create_provider
from chimera.acp.types import ACPSessionConfig
from chimera.acp.tool import ExternalAgentTool

# 1. Configure external agent
config = ACPSessionConfig(
    command=["npx", "-y", "claude-code-acp"],
    working_dir="/my/project",
)

# 2. Wrap as tool
ext_tool = ExternalAgentTool(config=config, agent_name="claude_coder")

# 3. Build agents
provider = create_provider(model="claude-sonnet-4-20250514")

worker = Agent(
    provider=provider,
    tools=[ext_tool],
    prompt=Prompt.from_string("Use claude_coder to implement tasks."),
    name="implementer",
)

coordinator = Agent(
    provider=provider,
    prompt=Prompt.from_string("Delegate implementation to 'implementer'."),
)

# 4. Run
supervisor = Supervisor(coordinator=coordinator, workers={"implementer": worker})
result = supervisor.run("Add input validation to all API endpoints.", env=None)
print(result.output)

# 5. Cleanup
ext_tool.cleanup()
```

---

## Next steps

- [Compose Agents](/compose-agents/) -- Pipeline, Ensemble, and Supervisor patterns
- [Add a Custom Tool](/add-custom-tool/) -- build native Chimera tools

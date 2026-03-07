# Agent Client Protocol (ACP)

`chimera.acp` implements the Agent Client Protocol -- a JSON-RPC 2.0 interface
for communicating with external AI agents over subprocess stdio.  It lets a
Chimera agent delegate tasks to any ACP-compatible agent (including other
Chimera instances, Claude Code, or custom agents) and collect their responses,
tool calls, and cost data as first-class objects.

## Quick Start

```python
from chimera.acp import ACPClient, ACPSessionConfig

config = ACPSessionConfig(
    command=["python", "-m", "my_agent_server"],
    working_dir="/path/to/project",
)

with ACPClient(config) as client:
    response = client.send_message("Refactor the auth module")
    print(response.text)
    print(f"Cost: ${response.cost:.4f}")
    print(f"Tool calls: {len(response.tool_calls)}")
```

## Key Classes

| Class | Description |
|-------|-------------|
| `ACPSessionConfig` | Dataclass configuring the subprocess command, args, env, working directory, and notification drain delay |
| `ACPToolCall` | Dataclass representing a tool call made by the external agent, with id, title, kind, status, input/output, and error flag |
| `ACPResponse` | Dataclass accumulating the full response: text, thoughts, tool calls, cost, and token usage |
| `ACPClient` | JSON-RPC 2.0 client that spawns and communicates with an ACP server subprocess |
| `ExternalAgentTool` | Wraps an `ACPClient` as a Chimera `BaseTool` so external agents appear as regular tools |

## Usage

### ACPSessionConfig

Configure how the ACP server subprocess is spawned:

```python
from chimera.acp import ACPSessionConfig

config = ACPSessionConfig(
    command=["npx", "-y", "claude-code-acp"],
    args=["--model", "claude-sonnet-4-20250514"],
    env={"ANTHROPIC_API_KEY": "sk-ant-..."},
    working_dir="/path/to/repo",
    notification_drain_delay=0.1,
)
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `command` | `list[str]` | `[]` | Command to spawn the ACP server |
| `args` | `list[str]` | `[]` | Additional CLI arguments |
| `env` | `dict[str, str]` | `{}` | Extra environment variables (merged with `os.environ`) |
| `working_dir` | `str \| None` | `None` | Working directory for the subprocess |
| `notification_drain_delay` | `float` | `0.1` | Seconds to wait for trailing notifications |

### ACPClient

The client manages the subprocess lifecycle and JSON-RPC communication:

```python
from chimera.acp import ACPClient, ACPSessionConfig

config = ACPSessionConfig(command=["python", "-m", "my_agent"])
client = ACPClient(config)

# Manual lifecycle
client.start()       # Spawns subprocess, creates session
response = client.send_message("Write unit tests for utils.py")
client.stop()        # Terminates subprocess

# Context manager (recommended)
with ACPClient(config) as client:
    response = client.send_message("Fix the failing CI tests")
```

#### Streaming chunks

Pass an `on_chunk` callback to `send_message()` for real-time streaming:

```python
def print_chunk(chunk: str):
    print(chunk, end="", flush=True)

with ACPClient(config) as client:
    response = client.send_message(
        "Explain the architecture",
        on_chunk=print_chunk,
    )
```

#### Forking sessions

Fork a session to run parallel queries that share the same conversation
history up to the fork point:

```python
with ACPClient(config) as client:
    client.send_message("Read the codebase and understand the architecture")

    # Fork for parallel exploration
    fork_id = client.fork_session()
    # The new session_id can be used with the same subprocess
```

### ACPResponse

The response object accumulates all data from a `send_message()` call:

| Field | Type | Description |
|-------|------|-------------|
| `text` | `str` | Full text response from the external agent |
| `thoughts` | `list[str]` | Reasoning/thought chunks (if the agent emits them) |
| `tool_calls` | `list[ACPToolCall]` | Tool calls made during the response |
| `cost` | `float` | Total cost of the response in USD |
| `input_tokens` | `int` | Number of input tokens consumed |
| `output_tokens` | `int` | Number of output tokens generated |

### ACPToolCall

Each tool call made by the external agent is captured as an `ACPToolCall`:

| Field | Type | Description |
|-------|------|-------------|
| `tool_call_id` | `str` | Unique identifier |
| `title` | `str` | Human-readable title |
| `tool_kind` | `str` | Kind/category of tool |
| `status` | `str` | `"running"`, `"completed"`, or `"error"` |
| `raw_input` | `str \| None` | Raw input sent to the tool |
| `raw_output` | `str \| None` | Raw output from the tool |
| `is_error` | `bool` | Whether the tool call failed |

### ExternalAgentTool

`ExternalAgentTool` wraps an ACP agent as a standard Chimera `BaseTool`.
This means you can add external agents to any agent's tool list and the
model will be able to delegate tasks to them.

```python
from chimera.acp import ExternalAgentTool, ACPSessionConfig
from chimera.core.agent import Agent
from chimera.providers import create_provider

# Configure the external agent
config = ACPSessionConfig(
    command=["npx", "-y", "claude-code-acp"],
    working_dir="/path/to/frontend",
)

# Create the tool
frontend_agent = ExternalAgentTool(
    config=config,
    agent_name="frontend_expert",
)

# Add it to an agent's tool list
provider = create_provider("anthropic")
agent = Agent(
    provider=provider,
    tools=[frontend_agent],  # model sees "frontend_expert" as a callable tool
)
```

The tool accepts a single `task` argument and returns a `ToolResult` whose
`output` is the agent's text response.  The `metadata` dict contains
`thoughts`, `tool_calls`, `cost`, `input_tokens`, and `output_tokens`.

Lifecycle methods:

| Method | Description |
|--------|-------------|
| `setup()` | Starts the ACP client subprocess (called automatically on first `execute()`) |
| `execute(args, env)` | Sends `args["task"]` to the agent and returns a `ToolResult` |
| `cleanup()` | Stops the ACP client subprocess |

### JSON-RPC 2.0 protocol

Under the hood, `ACPClient` uses these JSON-RPC 2.0 methods:

| RPC Method | Purpose |
|-----------|---------|
| `session/create` | Create a new session with a working directory |
| `session/sendMessage` | Send a message and receive response + notifications |
| `session/fork` | Fork an existing session for parallel work |

Notifications received during `sendMessage`:

| Notification | Data |
|-------------|------|
| `agent/messageChunk` | Streaming text chunk |
| `agent/thoughtChunk` | Reasoning/thought chunk |
| `agent/toolCallStart` | Tool call initiated (id, title, kind) |
| `agent/toolCallComplete` | Tool call finished (output, is_error) |
| `agent/usageUpdate` | Cost and token usage update |

## Integration

ACP connects to the rest of Chimera in several ways:

- **ExternalAgentTool as BaseTool**: Any loop or agent that accepts tools can
  use external agents. This is the primary integration path.

- **Supervisor composition**: The `Supervisor` composition pattern can use
  `ExternalAgentTool` instances as worker agents, enabling multi-agent
  orchestration across process boundaries.

- **EventBus**: When an `ExternalAgentTool` is used within a loop that has an
  `EventBus`, the framework publishes `ExternalAgentStartEvent`,
  `ExternalAgentToolCallEvent`, and `ExternalAgentCompleteEvent`:

```python
from chimera.events.types import (
    ExternalAgentStartEvent,      # agent_name, task
    ExternalAgentToolCallEvent,   # agent_name, tool_call_id, title, status
    ExternalAgentCompleteEvent,   # agent_name, response_text, cost, tool_calls_count
)
```

## Import Reference

```python
from chimera.acp import (
    ACPSessionConfig,
    ACPToolCall,
    ACPResponse,
    ACPClient,
    ExternalAgentTool,
)
```

# Wire

`chimera.wire` provides a bidirectional communication channel between an agent
and a UI (terminal, web app, IDE extension, etc.).  The agent side sends
fire-and-forget messages and blocking requests; the UI side receives them via
callbacks and can respond.

## Quick Start

```python
import chimera
from chimera.wire.types import StepBegin, StepEnd, StatusUpdate

wire = chimera.Wire()

def on_message(msg):
    if isinstance(msg, StepBegin):
        print(f"Step {msg.step} starting...")
    elif isinstance(msg, StepEnd):
        print(f"Step {msg.step} done (tool: {msg.tool_name})")
    elif isinstance(msg, StatusUpdate):
        print(f"Status: step={msg.step}, cost=${msg.total_cost:.4f}")

wire.on_message(on_message)

# Connect Wire through LoopConfig
config = chimera.LoopConfig(wire=wire)

agent = chimera.Agent(
    provider=chimera.create_provider(),
    tools=list(chimera.AGENT_TOOLS),
    loop=chimera.ReAct(max_steps=10, config=config),
)

result = agent.run("Create hello.py and run it.", env=env)
```

## Message Types

Wire defines two categories of messages: fire-and-forget messages and
request/response pairs.

### Fire-and-forget

| Message | Fields | Description |
|---------|--------|-------------|
| `TurnBegin` | `turn_id` | A new agent turn started |
| `TurnEnd` | `turn_id`, `steps`, `output` | Agent turn completed |
| `StepBegin` | `step` | A ReAct loop step started |
| `StepEnd` | `step`, `tool_name`, `tool_args`, `tool_result` | Step completed, with tool call details |
| `StatusUpdate` | `context_tokens`, `max_tokens`, `total_cost`, `step`, `metadata` | Periodic agent status |

### Request/Response

| Request | Response | Description |
|---------|----------|-------------|
| `ApprovalRequest(tool_name, tool_args)` | `ApprovalResponse(approved, reason)` | Ask the UI to approve a tool call |
| `UserQuestion(question, choices)` | `UserAnswer(answer)` | Ask the user a question |

Requests block until the UI calls `wire.respond()` or the timeout expires
(raising `WireTimeout`).

## Wire API

```python
wire = Wire(event_bus=None)

# UI side: listen to all messages
wire.on_message(callback)

# Agent side: fire-and-forget
wire.send(StepBegin(step=1))

# Agent side: blocking request
response = wire.request(ApprovalRequest(tool_name="bash", tool_args={...}))

# UI side: respond to a request
wire.respond(ApprovalResponse(request_id=req.request_id, approved=True))

# Check pending requests
wire.pending_requests  # int
```

## Integration with LoopConfig

Pass a `Wire` instance through `LoopConfig` so the ReAct loop emits
`StepBegin` and `StepEnd` messages automatically:

```python
wire = chimera.Wire()
config = chimera.LoopConfig(wire=wire)
loop = chimera.ReAct(max_steps=10, config=config)
```

If you also pass an `EventBus`, Wire will publish messages to the bus
in addition to calling listeners:

```python
from chimera.events import EventBus

bus = EventBus()
wire = chimera.Wire(event_bus=bus)
```

## Import Reference

```python
from chimera.wire.wire import Wire, WireTimeout
from chimera.wire.types import (
    WireMessage, WireRequest, WireResponse,
    TurnBegin, TurnEnd, StepBegin, StepEnd,
    ApprovalRequest, ApprovalResponse,
    UserQuestion, UserAnswer,
    StatusUpdate,
)
```

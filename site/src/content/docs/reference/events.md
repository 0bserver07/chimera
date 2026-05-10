---
title: "chimera.events"
description: "Reference for chimera.events — EventBus, event types, middleware."
---

`chimera.events` is the loop's observability surface: every meaningful
state change emits a typed `Event` to a shared `EventBus`, and you can
subscribe with `@bus.on(type)` or `@bus.on("*")`.

## Top-level exports

```python
from chimera.events import EventBus
from chimera.events.types import (
    # Core
    ToolCallEvent, ToolResultEvent, StepEvent, TextDeltaEvent, ErrorEvent,
    LoopDetectedEvent, PermissionEvent, SessionEvent, StepCostEvent,
    # Lifecycle (added in pi-mono)
    AgentStartEvent, AgentEndEvent,
    TurnStartEvent, TurnEndEvent,
    StreamStartEvent, StreamEndEvent,
    ModelRequestEvent, ModelResponseEvent,
    # Advanced
    CompactionEvent, CriticEvent,
    ExternalAgentStartEvent, ExternalAgentCompleteEvent, ExternalAgentToolCallEvent,
    SecurityEvent, SteeringEvent, CancellationEvent,
)
```

26 event types are emitted; the table below lists the lifecycle subset
added during pi-mono adoption (the others — `ToolCallEvent`, etc. — are
unchanged).

## Lifecycle events (added in pi-mono)

| Class | `type` | Key fields |
|---|---|---|
| `AgentStartEvent` | `agent_start` | `max_steps` |
| `AgentEndEvent` | `agent_end` | `steps`, `success`, `total_cost` |
| `TurnStartEvent` | `turn_start` | `turn_number` |
| `TurnEndEvent` | `turn_end` | `turn_number`, `tool_calls_count` |
| `StreamStartEvent` | `stream_start` | `model` |
| `StreamEndEvent` | `stream_end` | `total_tokens` |
| `ModelRequestEvent` | `model_request` | `model`, `message_count`, `tool_count` |
| `ModelResponseEvent` | `model_response` | `model`, `content_length`, `tool_calls_count`, `input_tokens`, `output_tokens` |
| `SteeringEvent` | `steering` | `content` |
| `CancellationEvent` | `cancellation` | `at_step` |

All ten are exported from `chimera.events.types` and included in
`__all__`.

## EventBus API

```python
from chimera.events import EventBus

bus = EventBus()

@bus.on("tool_call")
def on_tool(event):
    print(f"[call] {event.tool_name}")

@bus.on("*")  # subscribe to everything
def on_any(event):
    print(f"[{event.type}] {event}")

bus.emit(ToolCallEvent(...))  # usually called by the loop, not user code
```

| Method | Purpose |
|---|---|
| `on(type_or_star)` | Decorator that registers a listener. |
| `emit(event)` | Dispatch an event to listeners. |
| `add_middleware(mw)` | Insert middleware that can transform events before dispatch (used by `RedactionMiddleware` from `chimera.secrets`). |
| `clear()` | Drop all listeners (test helper). |

## Wiring into a loop

```python
from chimera import Agent, DEFAULT_TOOLS, EventBus, LoopConfig, ReAct, create_provider

bus = EventBus()
config = LoopConfig(event_bus=bus)
loop = ReAct(max_steps=30, config=config)

agent = Agent(provider=create_provider(model="glm-5"),
              tools=list(DEFAULT_TOOLS), loop=loop)
```

## See also

- [`chimera.core`](/reference/core/) for `LoopConfig` and the lifecycle
  hooks the loop emits at.
- [`chimera.secrets`](/reference/secrets/) for the redaction middleware.
- [Configure Permissions](/configure-permissions/) for `PermissionEvent`
  monitoring patterns.

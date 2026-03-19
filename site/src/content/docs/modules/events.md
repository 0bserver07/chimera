---
title: "Events"
description: "Events"
---

`chimera.events` provides a lightweight publish/subscribe event bus with
middleware support.  It is used throughout the framework to broadcast lifecycle
events -- tool calls, streaming deltas, permission decisions, and more --
without coupling the emitter to the consumer.

## Core types

### Event (base dataclass)

Every event inherits from `Event`, which carries three fields:

| Field | Type | Description |
|-------|------|-------------|
| `type` | `str` | Discriminator string (e.g. `"tool_call"`, `"error"`) |
| `timestamp` | `float` | Monotonic timestamp set at creation via `time.monotonic` |
| `metadata` | `dict[str, Any]` | Arbitrary extra data |

### EventBus

The central hub.  Handlers are registered for a specific event type string, or
for `"*"` to receive every event.

| Method | Description |
|--------|-------------|
| `subscribe(event_type, handler)` | Register a handler; returns an unsubscribe callable |
| `on(event_type)` | Decorator form of `subscribe` |
| `publish(event)` | Dispatch to exact-type handlers **and** wildcard `"*"` handlers |
| `use(middleware)` | Append a `Middleware` to the processing chain |
| `clear()` | Remove all handlers and middleware |

## Event types

Nine concrete event dataclasses are defined in `chimera.events.types`:

| Class | `type` field | Key fields |
|-------|-------------|------------|
| `ToolCallEvent` | `tool_call` | `tool_name`, `arguments`, `call_id` |
| `ToolResultEvent` | `tool_result` | `call_id`, `output`, `success` |
| `StepEvent` | `step` | `step_number`, `content` |
| `TextDeltaEvent` | `text_delta` | `content` |
| `ErrorEvent` | `error` | `error`, `recoverable` |
| `LoopDetectedEvent` | `loop_detected` | `pattern` |
| `CompactionEvent` | `compaction` | `messages_before`, `messages_after` |
| `PermissionEvent` | `permission` | `tool_name`, `action`, `granted` |
| `SessionEvent` | `session` | `action`, `session_id` |

## Middleware

Middleware wraps the dispatch pipeline.  Each middleware implements a single
method:

```python
class Middleware(ABC):
    @abstractmethod
    def process(self, event: Event, next_handler: Callable[[Event], None]) -> None: ...
```

Two built-in implementations are provided:

- **`LoggingMiddleware`** -- logs every event's type and timestamp at DEBUG
  level before forwarding.
- **`FilterMiddleware`** -- only forwards events whose `type` is in the given
  `allow_types` set.

When multiple middleware are registered via `use()`, they are chained
outermost-first: the last middleware added wraps around all earlier ones.

## Examples

### Subscribing to events

```python
from chimera.events import EventBus, ToolCallEvent

bus = EventBus()

# Function-based subscription
def on_tool(event: ToolCallEvent):
    print(f"Tool called: {event.tool_name}")

unsub = bus.subscribe("tool_call", on_tool)

# Decorator-based subscription
@bus.on("error")
def on_error(event):
    print(f"Error: {event.error}")
```

### Publishing events

```python
bus.publish(ToolCallEvent(tool_name="bash", arguments={"command": "ls"}))
```

### Adding middleware

```python
from chimera.events import LoggingMiddleware, FilterMiddleware

bus.use(LoggingMiddleware())
bus.use(FilterMiddleware(allow_types={"tool_call", "error"}))
```

### Wildcard handler

```python
@bus.on("*")
def catch_all(event):
    print(f"[{event.type}] at {event.timestamp}")
```

### Unsubscribing

```python
unsub = bus.subscribe("step", my_handler)
# Later...
unsub()  # my_handler will no longer be called
```

---
title: "Streaming"
description: "Streaming"
---

`chimera.streaming` adds real-time output handling to agent loops.  It defines
a `StreamHandler` ABC, three concrete handlers, a `StreamingProvider` protocol,
and a `StreamingReAct` loop that wires everything together.

## StreamHandler (ABC)

The base class declares six hooks that are called at different points during
an agent run:

| Method | Called when |
|--------|-----------|
| `on_text(text)` | A text content delta is received |
| `on_tool_start(tool_name, call_id)` | A tool call begins |
| `on_tool_end(call_id, output)` | A tool call completes |
| `on_step_start(step)` | A ReAct step starts |
| `on_step_end(step)` | A ReAct step finishes |
| `on_done()` | The entire run is complete |

The concrete `handle_event(event)` method dispatches a `StreamEvent` to the
appropriate hook automatically, so handlers can also be driven directly by
provider-level stream events.

## Built-in handlers

### ConsoleStreamHandler

Prints human-readable output to stdout.  Text deltas are printed inline; tool
calls and steps are bracketed with markers.

```python
from chimera.streaming import ConsoleStreamHandler

handler = ConsoleStreamHandler()
# Output:
# --- Step 1 ---
# Let me check the file...
# [Tool: read_file]
# [Result: contents of the file...]
```

### CollectStreamHandler

Collects all events into a `list[dict]` for testing and programmatic
inspection.  Each dict has a `"type"` key plus event-specific fields.

```python
from chimera.streaming import CollectStreamHandler

handler = CollectStreamHandler()
# After a run:
# handler.events -> [{"type": "step_start", "step": 1}, {"type": "text", ...}, ...]
```

### NullStreamHandler

Silently discards every event.  Useful as a default when no streaming output
is desired.

## StreamingProvider protocol

A structural subtype (Python `Protocol`) for providers that expose a
`stream()` method:

```python
class StreamingProvider(Protocol):
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]: ...
```

Any provider that implements `stream()` satisfies this protocol without
explicit inheritance.

## StreamingReAct loop

`StreamingReAct` is a drop-in replacement for the standard `ReAct` loop that
consumes `StreamEvent` objects incrementally when the provider supports
streaming.  If the provider lacks a `stream()` method, it falls back to the
regular `complete()` path transparently.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_steps` | 50 | Maximum number of ReAct iterations |
| `handler` | `None` | `StreamHandler` instance to receive events |

```python
from chimera.streaming import StreamingReAct, ConsoleStreamHandler

loop = StreamingReAct(
    max_steps=100,
    handler=ConsoleStreamHandler(),
)

result = loop.run(provider, tools, context, env)
```

The loop internally calls `_accumulate_stream()` to consume the iterator of
`StreamEvent` objects into a single `Response`, forwarding each event to the
handler as it arrives.

## Full example

```python
from chimera.core.agent import Agent
from chimera.core.prompt import Prompt
from chimera.providers.anthropic import AnthropicProvider
from chimera.streaming import StreamingReAct, ConsoleStreamHandler

provider = AnthropicProvider()
loop = StreamingReAct(max_steps=50, handler=ConsoleStreamHandler())

agent = Agent(
    provider=provider,
    tools=[read_file, bash, search],
    loop=loop,
    prompt=Prompt.from_string("You are a helpful coding assistant."),
)

result = agent.run("Find all TODO comments in the project.")
```

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/streaming/` -- verified against current source at wave-17.

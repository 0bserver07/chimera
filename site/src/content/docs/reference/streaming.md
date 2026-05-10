---
title: "chimera.streaming"
description: "Reference for chimera.streaming — stream handlers and StreamingReAct."
---

`chimera.streaming` lets the agent surface partial output (text deltas
+ in-progress tool calls) as the LLM streams them.

## Top-level exports

```python
from chimera.streaming import (
    StreamHandler,
    ConsoleStreamHandler,
    BufferingStreamHandler,
    StreamingReAct,
)
```

| Symbol | Purpose |
|---|---|
| `StreamHandler` | ABC. `on_text_delta(text)`, `on_tool_call_start(call)`, `on_tool_call_complete(call)`, `on_step_end()`. |
| `ConsoleStreamHandler` | Prints text deltas to stdout as they arrive. |
| `BufferingStreamHandler` | Buffers everything, exposes `.text`, `.tool_calls` after the stream ends. |
| `StreamingReAct` | `ReAct` subclass that accepts a `StreamHandler` and forwards every delta. |

Pair with a streaming-capable provider (every built-in provider
implements `Provider.stream()`).

## See also

- [`chimera.events`](/reference/events/) for `StreamStartEvent` /
  `StreamEndEvent` / `TextDeltaEvent`.
- [`chimera.core`](/reference/core/) for `Agent.iter_steps()`.

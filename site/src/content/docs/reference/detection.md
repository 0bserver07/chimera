---
title: "chimera.detection"
description: "Reference for chimera.detection — loop-detection strategies (exact repeat, pattern cycle)."
---

`chimera.detection` watches the agent's tool-call stream for repetition
patterns that indicate the loop is stuck and signals the runtime to
break out (raising a `LoopDetectedEvent`).

## Top-level exports

```python
from chimera.detection import (
    LoopDetector,
    ExactRepeatDetector,
    PatternCycleDetector,
)
```

| Symbol | Purpose |
|---|---|
| `LoopDetector` | ABC. `observe(tool_call)` returns `True` when a loop is detected. |
| `ExactRepeatDetector(window=N)` | Flags when the same tool call is emitted N times in a row. |
| `PatternCycleDetector(min_period=2, min_repeats=3)` | Flags when a repeating multi-call pattern is detected. |

Wire into a loop via `LoopConfig.detector=`.

## See also

- [`chimera.events`](/reference/events/) for `LoopDetectedEvent`.
- [`chimera.core`](/reference/core/) for the `LoopConfig` field.

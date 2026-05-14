---
title: "think — record reasoning without acting"
description: "A no-op tool the agent can call to write down its reasoning step. Useful for plan-and-execute and reflection loops; emits a step in the transcript so the next turn can refer to it."
---

`think` is a deliberate no-op. It writes the supplied `thought` into the run's transcript and returns immediately. No environment access, no side-effects — just a structured place to externalise reasoning so the next turn (or a reviewer) can see it.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `thought` | string | yes | The reasoning to record. |

## Example invocation

```json
{"thought": "The bug is in retry logic. Options: (a) cap retries, (b) backoff jitter. Picking (b) — simpler."}
```

```python
from chimera.tools.think import ThinkTool

tool = ThinkTool()
result = tool.execute(
    {"thought": "Need to check whether the env var is set before reading."},
    env=None,
)
print(result.metadata["thought"])
```

## Output sample

```
Thought recorded.
```

The actual text lives in `result.metadata["thought"]` and shows up in the [`StepCost`](/chimera/concepts/events/) event for the turn.

## When to use it

- Plan-and-execute loops — write the plan, then execute.
- Reflection / critique — capture self-correction without polluting the user-facing output.
- Long multi-step traces — anchor decisions for later review.

## See also

- [Loops](/chimera/concepts/loops/) — PlanAndExecute, Reflexion, TreeOfThought.
- [`todo`](./todo.md) — track multi-step work explicitly.

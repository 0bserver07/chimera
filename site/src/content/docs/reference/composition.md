---
title: "chimera.composition"
description: "Reference for chimera.composition — Pipeline (sequential), Ensemble (parallel), Supervisor (coordinator + workers)."
---

`chimera.composition` provides three patterns for combining agents.

For a tutorial walk-through, see [Compose Agents](/compose-agents/).

## Top-level exports

```python
from chimera import Pipeline, Ensemble, Supervisor
```

| Pattern | Use when | Cost |
|---|---|---|
| `Pipeline(agents=[...])` | Clearly ordered stages (plan → implement → review). Output of agent N is task of agent N+1. | Sequential. |
| `Ensemble(agents=[...])` | You want diversity and will pick the best result. | N agents in parallel. |
| `Supervisor(coordinator=..., workers={name: agent, ...})` | Coordinator delegates sub-tasks. Each worker is exposed to the coordinator as a `DelegateTool`. | Whatever the coordinator chooses. |

All three return `AgentResult` (or `list[AgentResult]` from `Ensemble`),
so they nest: a `Pipeline` whose first stage is a `Supervisor`, an
`Ensemble` of `Pipeline`s, etc.

`Ensemble.best(results)` returns the first successful result by
default; subclass or write your own selector for cost / quality tradeoffs.

## See also

- [Compose Agents](/compose-agents/) for end-to-end examples.
- [`chimera.core`](/reference/core/) for the `Agent` class each pattern wraps.

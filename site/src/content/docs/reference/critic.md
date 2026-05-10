---
title: "chimera.critic"
description: "Reference for chimera.critic — Critic ABC, LLM and checklist critics, mixin for iterative refinement."
---

`chimera.critic` lets a separate LLM (or rule-based check) review the
agent's actions and request revisions before the loop accepts them.

## Top-level exports

```python
from chimera.critic import (
    Critic,
    CriticResult,
    CriticConfig,
    CriticMode,
    LLMCritic,
    ChecklistCritic,
    CriticMixin,
)
```

| Symbol | Module | Purpose |
|---|---|---|
| `Critic` | `chimera.critic.base` | ABC. Override `evaluate(action, context) -> CriticResult`. |
| `CriticResult` | `chimera.critic.base` | Dataclass: `passed`, `feedback`, `severity`. |
| `CriticConfig` | `chimera.critic.base` | Mode + threshold + max-iterations. |
| `CriticMode` | `chimera.critic.base` | Enum: `ALL_ACTIONS`, `FINISH_ONLY`. |
| `LLMCritic` | `chimera.critic.llm_critic` | Provider-backed critic. Pass any `Provider`. |
| `ChecklistCritic` | `chimera.critic.llm_critic` | Rule-based critic — runs a list of `Check` callables. |
| `CriticMixin` | `chimera.critic.mixin` | Loop integration with iterative refinement. |

Wire into a loop by mixing `CriticMixin` into your loop class or by
passing the critic via `LoopConfig.critic=`.

## See also

- [`chimera.events`](/reference/events/) for `CriticEvent`.
- [`chimera.review`](/modules/) for the `ReviewOrchestrator` workflow.

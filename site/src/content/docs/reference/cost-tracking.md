---
title: "chimera.providers.cost_tracker"
description: "Reference for chimera.providers.cost — pricing tables and per-step cost tracking."
---

Two modules cooperate to track LLM spend:

## Pricing (`chimera.providers.cost`)

```python
from chimera.providers.cost import register_model_cost, get_model_cost
```

Built-in pricing for every model the catalog ships (GLM, Claude,
DeepSeek, Kimi, Qwen3, GPT-OSS, ...). Register your own:

```python
register_model_cost("acme/llm", 0.50, 1.50)  # $/Mtok input, output
```

Returns input / output USD per million tokens.

## Tracker (`chimera.providers.cost_tracker`)

```python
from chimera.providers.cost_tracker import CostTracker
```

`CostTracker` records granular per-step token counts (cache hits,
reasoning tokens, regular input/output) and rolls them into a session
total.

| Method | Purpose |
|---|---|
| `record_step(usage)` | Append a step's token usage. |
| `total_tokens()` | Sum across all steps. |
| `total_cost()` | USD across all steps. |
| `breakdown()` | Per-step rows with cache / reasoning / regular splits. |

Wired into the loop via `LoopConfig.cost_tracker=` and surfaced in the
REPL via `/cost`.

## See also

- [`chimera.providers`](/reference/providers/) for the pricing table.
- [`chimera.events`](/reference/events/) for `StepCostEvent`.

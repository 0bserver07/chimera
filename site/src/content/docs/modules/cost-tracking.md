---
title: "Cost Tracking"
description: "Cost Tracking"
---

The cost tracking module provides granular, per-call token accounting with cache-aware pricing, per-step breakdowns, per-model summaries, budget enforcement, and context window utilization monitoring. Attach a `CostTracker` to an agent to track exactly how much each step costs, where cache hits save money, and how close you are to your budget.

## Quick Start

```python
from chimera.providers.cost_tracker import CostTracker

tracker = CostTracker(budget=5.00)

usage = tracker.record_usage(
    model="claude-sonnet-4",
    input_tokens=1000,
    output_tokens=500,
    cache_read_tokens=200,
)

print(f"Cost so far: ${tracker.total:.4f}")
print(f"Cache hit rate: {usage.cache_hit_rate:.1%}")
print(f"Budget remaining: ${tracker.remaining:.4f}")
```

## Key Classes

| Class | Module | Description |
|-------|--------|-------------|
| `TokenUsage` | `chimera.providers.cost_tracker` | Dataclass for a single LLM call: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`, `total_tokens`, `cost`, `model`, `timestamp`. Properties: `cache_hit_rate`, `effective_input_tokens`. |
| `StepUsage` | `chimera.providers.cost_tracker` | Aggregated usage for one agent step: `step_index`, `calls` (list of `TokenUsage`), `start_time`, `end_time`. Properties: `total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cache_write_tokens`, `total_reasoning_tokens`, `total_cost`, `duration`. |
| `CostTracker` | `chimera.providers.cost_tracker` | Main tracker. Records calls via `record()` or `record_usage()`, tracks per-model and per-step breakdowns, enforces budgets, and provides summary statistics. |
| `CostLimitExceeded` | `chimera.providers.cost_tracker` | Exception raised when the cost budget is exceeded. |

## Usage

### Basic cost recording

For simple cost tracking without token-level detail, use `record()`:

```python
from chimera.providers.cost_tracker import CostTracker

tracker = CostTracker(budget=10.00)
tracker.record(0.05, model="claude-sonnet-4")
tracker.record(0.12, model="gpt-4o")

print(tracker.total)           # 0.17
print(tracker.breakdown())     # {"claude-sonnet-4": 0.05, "gpt-4o": 0.12}
print(tracker.remaining)       # 9.83
```

### Granular token tracking

Use `record_usage()` for full token-level accounting with automatic cost calculation:

```python
from chimera.providers.cost_tracker import CostTracker

tracker = CostTracker()

usage = tracker.record_usage(
    model="claude-sonnet-4",
    input_tokens=5000,
    output_tokens=1200,
    cache_read_tokens=3000,
    cache_write_tokens=500,
    reasoning_tokens=0,
)

print(f"This call cost: ${usage.cost:.4f}")
print(f"Cache hit rate: {usage.cache_hit_rate:.1%}")
print(f"Effective input tokens: {usage.effective_input_tokens}")
```

### Per-step tracking

Wrap agent steps with `start_step()` / `end_step()` to group LLM calls by step:

```python
tracker = CostTracker()

tracker.start_step(0)
tracker.record_usage(model="claude-sonnet-4", input_tokens=1000, output_tokens=200)
tracker.record_usage(model="claude-sonnet-4", input_tokens=1500, output_tokens=300)
step = tracker.end_step()

print(f"Step 0: {step.total_cost:.4f} USD, {step.duration:.2f}s")
print(f"Total calls in step: {len(step.calls)}")

# Find the most expensive step across the session
expensive = tracker.most_expensive_step()
```

### Budget enforcement

When a budget is set, `CostLimitExceeded` is raised as soon as the budget is exceeded:

```python
from chimera.providers.cost_tracker import CostTracker, CostLimitExceeded

tracker = CostTracker(budget=0.10)
try:
    tracker.record_usage(
        model="claude-opus-4",
        input_tokens=100_000,
        output_tokens=5_000,
    )
except CostLimitExceeded as e:
    print(f"Stopped: {e}")
```

### Full summary

The `summary()` method returns a comprehensive dict of all tracked metrics:

```python
info = tracker.summary()
# {
#     "total_cost": 0.17,
#     "total_calls": 5,
#     "total_input_tokens": 15000,
#     "total_output_tokens": 3200,
#     "total_cache_read_tokens": 8000,
#     "total_cache_write_tokens": 1000,
#     "total_reasoning_tokens": 0,
#     "cache_hit_rate": 0.348,
#     "context_utilization": 0.0,
#     "budget": 10.0,
#     "budget_remaining": 9.83,
#     "by_model": {"claude-sonnet-4": {...}, "gpt-4o": {...}},
#     "steps": 3,
#     "most_expensive_step": 1,
# }
```

### Usage update callback

Register a callback that fires after each recorded call:

```python
def on_update(usage):
    print(f"[{usage.model}] +${usage.cost:.4f}")

tracker = CostTracker(on_usage_update=on_update)
tracker.record_usage(model="claude-sonnet-4", input_tokens=500, output_tokens=100)
# prints: [claude-sonnet-4] +$0.0030
```

## Integration

- **Agent**: Each `Agent` instance holds a `CostTracker`. The ReAct loop calls `start_step()` / `end_step()` around each iteration and `record_usage()` after every provider call.
- **REPL `/cost` command**: Displays the current tracker's `summary()` in a formatted table, including per-model breakdown, cache stats, and budget status.
- **EventBus**: A `StepCost` event is emitted after each step with the `StepUsage` data, enabling middleware and plugins to react to cost changes.
- **Provider layer**: Providers call `record_usage()` on the tracker after each LLM response, passing token counts from the API response headers.
- **Built-in pricing**: `MODEL_PRICING` includes per-million-token rates for Claude (Opus, Sonnet, Haiku), GPT-4o, o1, and o3-mini, with distinct pricing for input, output, cache-read, cache-write, and reasoning tokens. Unknown models fall back to the `"default"` pricing tier.

## Import Reference

```python
from chimera.providers.cost_tracker import (
    CostLimitExceeded,
    CostTracker,
    StepUsage,
    TokenUsage,
)
```

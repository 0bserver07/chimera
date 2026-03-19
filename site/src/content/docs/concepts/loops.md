---
title: "Loops"
description: "Loops"
---

A **Loop** defines the execution strategy an agent follows -- how it reasons, when it invokes tools, and when it stops. Chimera ships four loop variants, each suited to different problem types.

## Available Loops

| Loop | Module | Strategy |
|------|--------|----------|
| `ReAct` | `chimera.core.loop` | Reason, Act, Observe -- the default |
| `PlanAndExecute` | `chimera.core.loops.plan_execute` | Generate a plan first, then execute it |
| `Reflexion` | `chimera.core.loops.reflexion` | Act, then periodically reflect and improve |
| `TreeOfThought` | `chimera.core.loops.tree_of_thought` | Generate N candidates, evaluate, pick the best |

All loops share the same interface:

```python
def run(
    self,
    provider: Provider,
    tools: list[BaseTool],
    context: Context,
    env: Environment | None,
) -> AgentResult:
```

## ReAct (Default)

**ReAct** (Reason + Act) is the standard agentic loop. At each step:

1. **Reason** -- the model produces text and/or tool calls
2. **Act** -- tool calls are executed against the environment
3. **Observe** -- tool results are added to the context
4. **Repeat** -- until the model responds with no tool calls, or `max_steps` is reached

```python
from chimera.core.loop import ReAct

loop = ReAct(max_steps=50)
```

The loop terminates with `success=True` when the model produces a final text-only response (no tool calls). It terminates with `success=False` when `max_steps` is exhausted or a loop is detected.

## PlanAndExecute

**PlanAndExecute** splits reasoning into two explicit phases:

1. **Phase 1 (Plan)** -- the model generates a plan (text-only response, no tools)
2. **Phase 2 (Execute)** -- a follow-up prompt ("Now execute the plan you just created, step by step.") triggers ReAct-style tool execution

```python
from chimera.core.loops.plan_execute import PlanAndExecute

loop = PlanAndExecute(max_steps=50)
```

:::tip[When to use PlanAndExecute]
Use this loop for complex, multi-step tasks where you want the model to think through the full approach before taking action. It reduces "wandering" on tasks with many interdependent steps.
:::## Reflexion

**Reflexion** adds periodic self-reflection to the standard loop. After every `reflect_every` tool calls, the model is asked:

> "Reflect on what you just did. What worked? What didn't? What should you do differently in the next step?"

```python
from chimera.core.loops.reflexion import Reflexion

loop = Reflexion(max_steps=50, reflect_every=3)
```

The reflection is injected as a user message, prompting the model to course-correct. This is effective for tasks where agents tend to get stuck in unproductive patterns.

## TreeOfThought

**TreeOfThought** generates multiple candidate responses at each step and selects the best one:

1. Generate `n_candidates` responses (at temperature 0.7)
2. If any candidate includes tool calls, execute the first one with tool calls
3. If all candidates are text-only, ask the model to evaluate and pick the best
4. Continue from the chosen candidate

```python
from chimera.core.loops.tree_of_thought import TreeOfThought

loop = TreeOfThought(max_steps=50, n_candidates=3)
```

:::caution[Cost considerations]
TreeOfThought calls the LLM N times per step (plus an evaluation call), so it costs significantly more than ReAct. Use it for high-value tasks where quality matters more than cost.
:::## LoopConfig

`LoopConfig` is a dataclass that injects optional behaviors into any loop variant. All fields default to `None`, so the loop works without any configuration.

```python
from chimera.core.loop_config import LoopConfig

config = LoopConfig(
    permissions=my_permission_policy,     # Human-in-the-loop approval
    detector=my_loop_detector,            # Detect infinite loops
    compaction=my_compaction_strategy,    # Compact long conversations
    handler=my_stream_handler,            # Stream tokens to the UI
    event_bus=my_event_bus,               # Publish step events
    auto_compact_threshold=0.8,           # Compact at 80% of context window
)

loop = ReAct(max_steps=50, config=config)
```

| Field | Type | Purpose |
|-------|------|---------|
| `permissions` | `PermissionPolicy` | Gate tool execution behind human approval |
| `detector` | `LoopDetector` | Detect and break infinite loops |
| `compaction` | `CompactionStrategy` | Summarize old messages when context gets long |
| `handler` | `StreamHandler` | Stream text and tool call events to the UI |
| `event_bus` | `EventBus` | Publish `StepEvent` for observability |
| `auto_compact_threshold` | `float` | Trigger compaction at this fraction of context window (default: 0.8) |

All four loops (`ReAct`, `PlanAndExecute`, `Reflexion`, `TreeOfThought`) accept a `config` parameter.

## When to Choose Which Loop

| Scenario | Recommended Loop |
|----------|-----------------|
| General-purpose coding tasks | `ReAct` (default) |
| Complex multi-step refactors | `PlanAndExecute` |
| Tasks where the agent keeps making the same mistakes | `Reflexion` |
| High-stakes tasks where you want multiple approaches explored | `TreeOfThought` |
| You want minimal cost | `ReAct` with low `max_steps` |

## Code Example: Full Configuration

```python
from chimera.core.agent import Agent
from chimera.core.loop_config import LoopConfig
from chimera.core.loops.reflexion import Reflexion
from chimera.providers.factory import create_provider
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.local import LocalEnvironment

provider = create_provider(model="claude-sonnet-4-20250514")

config = LoopConfig(
    auto_compact_threshold=0.8,
)

agent = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),
    loop=Reflexion(max_steps=100, reflect_every=5, config=config),
)

with LocalEnvironment(workdir="./project") as env:
    result = agent.run("Refactor the database module to use async/await", env)
    print(f"Completed in {result.steps} steps (cost: ${result.cost:.4f})")
```

## API Reference

- `chimera.core.loop.ReAct` -- default reasoning loop
- `chimera.core.loops.plan_execute.PlanAndExecute` -- plan-first loop
- `chimera.core.loops.reflexion.Reflexion` -- reflective loop
- `chimera.core.loops.tree_of_thought.TreeOfThought` -- multi-candidate loop
- `chimera.core.loop_config.LoopConfig` -- optional behavior injection

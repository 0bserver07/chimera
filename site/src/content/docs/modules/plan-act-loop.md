---
title: "Plan/Act Loop"
description: "Plan/Act Loop"
---

`chimera.core.loops.plan_act` implements a two-phase loop inspired by
[Cline's Plan/Act mode](https://github.com/cline/cline). The agent first
explores the codebase with read-only tools and produces a step-by-step plan,
then executes the plan with full tool access in a separate context.

Unlike `PlanAndExecute` which runs in a single context, `PlanActLoop` creates
**separate contexts** for each phase, preventing accidental mutations during
exploration.

## Key Classes

| Class | Description |
|-------|-------------|
| `PlanActLoop` | Two-phase loop: read-only planning, then full execution |
| `READ_ONLY_TOOLS` | Set of tool names safe for planning: `read_file`, `search`, `list_files`, `repo_map`, `think`, `read_image` |

## Quick Start

```python
from chimera.core.loops.plan_act import PlanActLoop

loop = PlanActLoop(plan_steps=10, act_steps=25)
result = loop.run(provider, tools, context, env)

# The plan generated in phase 1 is available after run()
print(loop.plan_output)
```

## How It Works

1. **Plan phase**: The agent receives the user's task with a "PLANNING PHASE"
   instruction. Only read-only tools are available. The agent explores the
   codebase and outputs a plan.

2. **Act phase**: A new context is built with the original task plus the plan
   output. The agent receives full tool access and executes the plan.

3. The combined `AgentResult` sums steps and cost from both phases.

## Custom Read-Only Tools

Override the default read-only tool set:

```python
loop = PlanActLoop(
    read_only_tools={"read_file", "search", "list_files", "think", "repo_map", "import_graph"},
    plan_steps=15,
    act_steps=30,
)
```

## Import Reference

```python
from chimera.core.loops.plan_act import PlanActLoop, READ_ONLY_TOOLS
```

## Related

- [Loops](/../concepts/loops/) -- overview of all loop types
- [Retry Loop](/retry-loop/) -- retry wrapper with scoring
- [Lint Feedback Loop](/lint-feedback-loop/) -- linter-driven iteration

# Build Cline in 70 Lines

The upstream we're modelling is Cline: a coding agent with an explicit plan-then-act split. The agent doesn't just barrel into the codebase — it first stops, surveys the situation, writes down what it intends to do, and only then starts editing.

In chimera primitives this is naturally a `Pipeline` of two agents: one with read-only tools that emits the plan, one with editing tools that follows it.

## What changes vs. SWE-Agent

The single ReAct loop becomes a `Pipeline` of two ReAct loops.

| Phase | Tools | Output |
|---|---|---|
| 1. PLAN | `read_file`, `search`, `list_files` | A numbered plan as text |
| 2. ACT | `read_file`, `write_file`, `edit_file`, `bash` | An applied fix + verification |

The plan from phase 1 becomes the task input for phase 2. That's the entire "plan mode → act mode" mechanism — `Pipeline` does the message passing.

## Full code

```python
"""Recreate a plan-act-execute coding agent (~70 lines) in chimera.

Posture: two phases connected by a Pipeline.
  Phase 1 (PLAN):  a read-only Explore agent surveys the repo and writes
                   a concrete, ordered fix plan.
  Phase 2 (ACT):   a Coding agent executes the plan and verifies via tests.

The plan from Phase 1 becomes the task input for Phase 2 — that's what
"plan mode -> act mode" looks like in chimera primitives.
"""
from __future__ import annotations

import os

os.environ.setdefault("OLLAMA_HOST", "https://ollama.com")

import chimera
from chimera.composition import Pipeline
from chimera.core.loop_config import LoopConfig
from chimera.tools.bash import BashTool
from chimera.tools.edit import EditFileTool
from chimera.tools.list_files import ListFilesTool
from chimera.tools.read import ReadFileTool
from chimera.tools.search import SearchTool
from chimera.tools.write import WriteFileTool

provider = chimera.create_provider(
    provider_type="ollama",
    model="glm-5.1:cloud",
)

# --- Phase 1: PLAN. Read-only tools, prompt demands a written plan. ---
planner = chimera.Agent(
    provider=provider,
    tools=[ReadFileTool(), SearchTool(), ListFilesTool()],
    loop=chimera.ReAct(max_steps=5, config=LoopConfig(yolo_mode=True)),
    prompt=chimera.Prompt.from_string(
        "You are the PLAN phase of a two-phase coding agent.\n"
        "Read files and produce a numbered, concrete plan to fix the bug.\n"
        "Output only the plan — do not edit anything.\n"
        "Format:\n"
        "  1. <step>\n"
        "  2. <step>\n"
        "  3. Verify by running <command>.\n"
    ),
    name="planner",
)

# --- Phase 2: ACT. Edits + bash. Receives plan from Phase 1 as its task. ---
actor = chimera.Agent(
    provider=provider,
    tools=[ReadFileTool(), WriteFileTool(), EditFileTool(), BashTool()],
    loop=chimera.ReAct(max_steps=6, config=LoopConfig(yolo_mode=True)),
    prompt=chimera.Prompt.from_string(
        "You are the ACT phase of a two-phase coding agent.\n"
        "Execute the numbered plan you receive. Verify with tests.\n"
        "Stop when tests pass."
    ),
    name="actor",
)

env = chimera.LocalEnvironment(".")
env.setup()

# Pipeline: planner.output -> actor.input.
pipeline = Pipeline(agents=[planner, actor])
result = pipeline.run(
    "src/auth.py has a bug. tests/test_auth.py is failing. "
    "Plan the fix, then execute it and verify with tests.",
    env=env,
)
env.cleanup()

print(f"steps_total={result.steps} success={result.success} cost=${result.cost:.6f}")
print(f"output:\n{result.output}")
```

75 lines including imports, comments, and blank lines.

## Why a Pipeline (not a single loop with a plan-tool)

You could shoehorn this into one ReAct loop with a `ThinkTool` that "plans first." The downsides:

- The same context (and same model weights) hold both phases, so the plan can drift.
- The planner has full tool access, which defeats the read-only invariant.
- It's harder to swap a smaller / cheaper planner model.

A `Pipeline` of two `Agent` instances gives you those swaps for free. Want a `glm-5.1:cloud` planner and a `gpt-oss:120b-cloud` actor? Two `create_provider` calls. Want to bench the planner alone? Run `planner.run(...)` and discard the output.

## Verified output

Run against Ollama Cloud (`OLLAMA_HOST=https://ollama.com`, model `glm-5.1:cloud`) with the same buggy `src/auth.py` repo:

```text
=== PLAN PHASE ===
plan steps=2 cost=$0.003806
PLAN OUTPUT:
## Plan

1. **Identify the bug**: In `src/auth.py`, the `add` function on line 2 returns `a - b`
   instead of `a + b`. The comment even documents this bug.

2. **Fix the bug**: Change `return a - b` to `return a + b` on line 2 of `src/auth.py`.

3. **Verify**: Run `tests/test_auth.py` to confirm all three assertions pass:
   - `add(2, 3) == 5`
   - `add(0, 0) == 0`
   - `add(-1, 1) == 0`

This is a one-line fix: replace `-` with `+` in the return statement.

=== ACT PHASE ===
act steps=4 cost=$0.007792
ACT OUTPUT:
The fix is complete. Here's a summary:
- **Bug**: `src/auth.py` had `return a - b` instead of `return a + b` in the `add` function.
- **Fix**: Changed `return a - b` to `return a + b` and removed the now-obsolete comment.
- **Verification**: All three test assertions pass.

=== TOTAL: steps=6 cost=$0.011598 elapsed=20.5s ===
```

Two phases, six total LLM turns, $0.012 of inference, ~20s wall clock. The plan is visible and inspectable between phases — you can log it, gate it through a reviewer, or rerun the actor against an edited plan.

## When to pick this posture

- **Long-running tasks where a wrong turn is expensive.** The plan is a cheap, inspectable artifact you can sanity-check before any file changes.
- **You want to swap models per phase.** Cheap planner + capable actor; or vice versa for evaluation tasks.
- **Plan-mode parity with the upstream.** Useful when migrating workflows that already split planning from execution.

## Where to next

- [Build SWE-Agent in 60 lines](./build-swe-agent-in-60-lines.md) — single-loop autonomous posture.
- [Build Aider in 50 lines](./build-aider-in-50-lines.md) — diff-first conservative posture.
- [Build Your Own Coding Agent](./build-your-own-coding-agent.md) — longer walkthrough with REPL, cost, undo.

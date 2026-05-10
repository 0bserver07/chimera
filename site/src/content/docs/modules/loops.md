---
title: "Loops"
description: "Loops"
---

`chimera.core.loop` and `chimera.core.loops` ship the eight loop
variants the framework uses to drive tool-augmented LLM agents. All
of them accept a [`LoopConfig`](/modules/loop-config/) for cross-cutting
concerns (permissions, events, audit, cancellation, hooks…) and behave
identically when no config is supplied.

## Overview

| Loop | Module | Purpose |
|------|--------|---------|
| `ReAct` | `chimera.core.loop` | Reason→Act→Observe; the legacy default loop. Generator-based, supports `iter_steps`, `PendingApproval`, full `LoopConfig` surface. |
| `AgentLoop` | `chimera.core.agent_loop` | Modern async-generator loop powering `CodingAgent`. Yields `LoopEvent`s; integrates `StreamingToolExecutor`, `AbortSignal`, `LoopState`, `RETRY_POLICIES`. |
| `RetryLoop` | `chimera.core.loops.retry` | Wrap any inner loop with score-and-retry. |
| `LintFeedbackLoop` | `chimera.core.loops.lint_feedback` | Inject linter / type-check feedback after each step. |
| `PlanActLoop` | `chimera.core.loops.plan_act` | Two-phase: plan first (read-only tools), then act (full tools). |
| `PlanAndExecute` | `chimera.core.loops.plan_execute` | Plan once, then execute the plan one bullet at a time. |
| `Reflexion` | `chimera.core.loops.reflexion` | Self-reflect on failure and retry with a critique-augmented prompt. |
| `TreeOfThought` | `chimera.core.loops.tree_of_thought` | Branch-and-vote tree search across alternative continuations. |
| `AutonomousLoop` | `chimera.core.loops.autonomous` | Long-running loop for unattended runs (no per-step approval). |

## ReAct vs AgentLoop

These are the two production loops. Most user-facing CLIs route through
`AgentLoop` via `CodingAgent`; `ReAct` remains for direct
`Agent(provider, tools).run(...)` calls and for the deprecated
`--legacy-react` escape hatch.

| Trait | `ReAct` | `AgentLoop` |
|-------|---------|-------------|
| Style | Sync generator (`iter_steps`) + `async_iter_steps` | Async generator (`async for event in loop.run(...)`) |
| Yields | `StepResult` | `LoopEvent` (typed event union) |
| Pending approval | `StepResult.pending_approval` (caller resolves) | Hooked via `permission_checker` + `permission_context` |
| Cancellation | `LoopConfig.cancellation` (`CancellationToken`) | `abort_signal: AbortSignal` |
| Streaming | `LoopConfig.handler` | `stream=True` flag + `StreamingToolExecutor` |
| Hook integration | Fires `SessionStart` / `UserPromptSubmit` / `Stop[Failure]` lifecycle | Full pre/post tool-use surface via `hook_executor` + `hook_matchers` |
| Default in CLI | `chimera code --legacy-react` | `chimera code` (default) |

## Quick Start

### ReAct

```python
from chimera.core.loop import ReAct
from chimera.core.context import Context
from chimera.providers import create_provider

loop = ReAct(max_steps=20)
provider = create_provider("anthropic")
context = Context()
context.add_user("Refactor the auth module.")

result = None
for step in loop.iter_steps(provider, tools, context, env):
    print(step.message.content)
    if step.pending_approval:
        step.pending_approval.approve()
    if step.done:
        result = step
        break
```

### AgentLoop

```python
from chimera.core.agent_loop import AgentLoop
from chimera.types import Message

loop = AgentLoop()
async for event in loop.run(
    messages=[Message.user("Fix the failing test")],
    tools=tools,
    provider=provider,
    system_prompt="You are a careful coding agent.",
    max_turns=50,
):
    if event.type.name == "text":
        print(event.data, end="", flush=True)
```

## RetryLoop

Wraps any inner loop with a scorer and retries failed runs:

```python
from chimera.core.loops.retry import RetryLoop, RetryAttempt

inner = ReAct(max_steps=20)
retry = RetryLoop(inner=inner, max_attempts=3, scorer=my_scorer)
result = retry.run(provider, tools, context, env)
# result.attempts: list[RetryAttempt]
```

## PlanActLoop

Two-phase loop: read-only tool set during the plan phase, the full
tool set during the act phase. The split tool set is exported as
`READ_ONLY_TOOLS` from `chimera.core.loops.plan_act`.

## Reflexion

Self-reflective loop: when a run fails, the loop prompts the model to
critique the failure, then retries with the critique appended to the
context — implementing the "Reflexion" prompting pattern.

## TreeOfThought

Branches the conversation into N candidate continuations, scores each,
and proceeds along the best branch. Useful for tasks where local
greedy choice is unlikely to find the global optimum.

## AutonomousLoop

Long-running unattended loop with no per-step approval. Pair with
`PermissionRuleset(default=ALLOW)` and a `LoopDetector` to keep
runaway loops bounded.

## LintFeedbackLoop

Injects linter / type-check output between turns so the agent sees
diagnostics in-band:

```python
from chimera.core.loops.lint_feedback import LintFeedbackLoop

loop = LintFeedbackLoop(
    lint_command=["ruff", "check", "."],
    max_steps=20,
)
```

## Picking a Loop

| Goal | Loop |
|------|------|
| General coding session, IDE-shaped | `AgentLoop` (default in `CodingAgent`) |
| Programmatic `Agent.run(...)` with steps you can iterate | `ReAct` |
| Drive convergence with retries | `RetryLoop` wrapping `ReAct`/`AgentLoop` |
| Linter-driven iteration | `LintFeedbackLoop` |
| Plan-first, then execute | `PlanActLoop` (two-phase) or `PlanAndExecute` |
| Unattended benchmarks / batch runs | `AutonomousLoop` + `LoopConfig(yolo_mode=True)` |
| Self-critique on failure | `Reflexion` |
| Branch-and-vote search | `TreeOfThought` |

## Import Reference

```python
from chimera.core.loop import ReAct
from chimera.core.agent_loop import AgentLoop
from chimera.core.loops import (
    AutonomousLoop,
    LintFeedbackLoop,
    PlanActLoop,
    PlanAndExecute,
    READ_ONLY_TOOLS,
    Reflexion,
    RetryLoop,
    TreeOfThought,
)
```

## Related

- [LoopConfig](/modules/loop-config/) — cross-cutting configuration
- [Critic](/modules/critic/) — score-and-refine inside any loop
- [Cancellation](/modules/cancellation/) — cooperative cancel signal
- [Detection](/modules/detection/) — exact-repeat / pattern-cycle guards

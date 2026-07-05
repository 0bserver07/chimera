---
title: Agent × Benchmark Matrix — glm-5.2[1m] (2026-07-03)
description: First live many-to-many run of chimera bench-matrix — react vs plan-execute across HumanEval, MATH-500, and τ-bench on glm-5.2[1m].
---

# Agent × Benchmark Matrix — glm-5.2[1m]

First live run of `chimera bench-matrix` (the many-to-many runner): two Chimera
agents crossed against three benchmarks on **glm-5.2[1m]** (z.ai endpoint), one
grader / sandbox / budget per column. Small samples (2 / 2 / 1 tasks) — this is a
plumbing + comparative-signal demonstration, not a leaderboard number.

## Results (pass rate)

| Agent | HumanEval (2) | MATH-500 (2) | τ-bench airline (1) |
|---|---|---|---|
| **react** | 100% (2/2) | 100% (2/2) | — error (no tool use) |
| **plan-execute** | 0% (0/2) | 100% (2/2) | 100% (1/1, 22 tool calls) |

Cost: react ≈ $0.043, plan-execute ≈ $0.096 total. Per-cell data:
`data/matrix-glm52-live.json`.

## What the matrix surfaced

The point of the matrix is the *contrast*, and this run shows a real one — the
two agents are near mirror images:

- **react** (single-completion loop) answers HumanEval and MATH-500 directly and
  aces both, but **errors on τ-bench** — the airline task needs multi-turn tool
  use, and a one-shot completion produces no runnable interaction.
- **plan-execute** (multi-step, tool-using) is the opposite: it **fails HumanEval
  (0%)** — its plan/execute overhead hurt a task react solved trivially — but
  **solves τ-bench (100%, 22 tool calls)**, which react couldn't touch.

Neither agent dominates; the right agent depends on the benchmark. That is
exactly the controlled, reproducible comparison `chimera bench-matrix` exists to
make.

## Methodology

- Shape: `chimera bench-matrix --agents react,plan-execute --benchmarks human-eval,math500,tau-bench --model glm-5.2[1m]` (driven via the library API with `.env` loaded).
- Model: `glm-5.2[1m]` via `ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic`; the `[1m]` suffix is stripped on the wire.
- Per-task env: fresh temp-dir `LocalEnvironment`. Grader: each benchmark's own `evaluate()`.
- Samples: HumanEval 2, MATH-500 2, τ-bench 1 (bounded for a fast demo).

## Caveat + next step

Multi-step agents (plan-execute, and heavier full-agent presets like `codex`) run
**slowly** here because per-agent **budget enforcement is not yet wired** — the
loops run to their own step ceilings rather than a matrix-level tool-call / cost
budget, so a full 12-agent grid is impractical until that lands. That is the
concrete next task (see `docs/specs/agent-benchmark-matrix.tasks.md`): once budgets
are enforced per cell, larger agent × bench grids become practical.

## Raw run log (source of truth)

```text
START 3 agents x 3 benches on glm-5.2[1m]
CELL react x human-eval: 100% (2/2) status=completed cost=$0.0252 tool_calls=0
CELL react x math500: 100% (2/2) status=completed cost=$0.0179 tool_calls=0
CELL react x tau-bench:airline: 0% (0/0) status=error cost=$0.0000 tool_calls=0
CELL plan-execute x human-eval: 0% (0/2) status=completed cost=$0.0146 tool_calls=2
CELL plan-execute x math500: 100% (2/2) status=completed cost=$0.0233 tool_calls=3
CELL plan-execute x tau-bench:airline: 100% (1/1) status=completed cost=$0.0577 tool_calls=22
```

## 2026-07-04 addendum — expanded-roster confirmation pass

A follow-up live pass confirmed the roster additions (1 task per bench,
glm-5.2[1m]; raw cells below). It also caught three real defects, all fixed the
same day: the assembled-CodingAgent presets errored via the **async** client
(the SDK non-streaming guard — same bug as the sync path, fixed in
`AnthropicProvider._aclient`), the `chimera code` flagship preset was missing
from the roster (added as `coding-agent`, roster now 13), and the external
example `aider` id clobbered the built-in aider style (renamed `aider-cli` +
collision-guard test).

| Agent (new in roster) | HumanEval (1) | MATH-500 (1) | Note |
|---|---|---|---|
| swe-agent (style) | 100% | 100% | retry loop, 2 tool calls |
| cline (style) | 100% | 100% | plan_act, 6 tool calls |
| aider (style) | 0% | 0% | runs + uses tools; final answer fails grading (answer-extraction pattern, like plan-execute on HumanEval) |
| swebench (preset) | error → **fixed** | error → **fixed** | async-timeout bug; post-fix probe completes |
| coding-agent (preset, `chimera code`) | — | — | added post-run; live probe completes ('OK', $0.002) |

```text
CELL swebench x human-eval: 0% (0/1) status=error        (pre-fix)
CELL swebench x math500: 0% (0/1) status=error           (pre-fix)
CELL swe-agent x human-eval: 100% (1/1) cost=$0.0128 tools=2
CELL swe-agent x math500: 100% (1/1) cost=$0.0038 tools=0
CELL aider x human-eval: 0% (0/1) completed cost=$0.0193 tools=2
CELL aider x math500: 0% (0/1) completed cost=$0.0118 tools=4
CELL cline x human-eval: 100% (1/1) cost=$0.0336 tools=6
CELL cline x math500: 100% (1/1) cost=$0.0150 tools=1
```

## 2026-07-05 addendum — six never-run agents + the answer contract

Baseline cells for the six roster agents that had never been live-run
(1 task/bench, glm-5.2[1m], **pre-contract** raw prompts):

| Agent | HumanEval (1) | MATH-500 (1) |
|---|---|---|
| reflexion | 0% | 100% |
| tree-of-thought | 0% | 100% |
| full-tools | 0% | 100% |
| action-first | 0% | 0% |
| minimal | 0% | 0% |
| explore | 0% | 100% |

HumanEval 0% across every multi-step agent confirmed the answer-extraction
pattern, which led to **`FINAL_ANSWER_CONTRACT`** (a uniform prompt suffix,
default-on in `run_matrix`, identical for every agent → still controlled).
Live effect: **reflexion × HumanEval 0% → 100% (2/2)**. Two agents it does
*not* fix, now precisely characterized: **lint-loop** ends on lint commentary
by architecture — its artifact is the files it writes, so it needs env-based
grading (spec open question #1); **CodingAgentAdapter presets** (full-tools,
action-first, …) still 0% — their final-message extraction is a separate
follow-up. Bonus find from the diagnosis: `read_file` on a directory raised an
uncaught `IsADirectoryError` that could kill a whole run — fixed to return a
tool error.

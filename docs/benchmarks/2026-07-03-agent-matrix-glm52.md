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

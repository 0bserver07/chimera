---
title: "First Controlled Comparative Matrix — 2026-06-11"
description: "react vs plan-execute on a 3-task pool under identical budgets (GLM-5.1): the first end-to-end run of `chimera bench-compare` with ATIF trajectory emission."
---

# First Controlled Comparative Matrix — 2026-06-11

**Date:** 2026-06-11
**Model:** GLM-5.1 (api.z.ai, Anthropic-compatible)
**Agents:** `react`, `plan-execute` (same model, same tools, same budget)
**Budget per task:** ≤6 LLM calls, ≤15 tool calls, ≤150 s wall clock
**Task pool:** 3 self-asserting function-writing tasks (clamp / run-length / median)
**Total spend:** ≈ $0.05 across both configs

## Matrix

| Agent | Pass rate | Avg cost | Avg steps | Budget hits |
|---|---|---|---|---|
| react | 100.0% (3/3) | $0.0009 | 1.0 | 0/3 |
| plan-execute | 0.0% (0/3) | $0.0147 | 4.0 | 3/3 |

## Reading

This is a smoke-scale pool, so the numbers describe the *harness*, not
the agents' general ability — but the architectural signal is exactly
the kind the comparative methodology exists to surface:

- **react** answered each task in one turn for under a tenth of a cent:
  single-turn completion tasks reward the loop that just answers.
- **plan-execute** decomposed each task, spent every one of its 6 LLM
  calls executing the plan, and hit the budget on all three tasks —
  ~16× react's cost for 0 passes. A planning loop is pure overhead when
  the task fits in one turn. Budget hits are reported in their own
  column, never conflated with ordinary failures.

## Trajectories

`--emit-atif` produced one ATIF v1.7 trajectory per (agent, task); all
six validate (`ATIFReader.load`) and carry per-turn telemetry — e.g.
plan-execute on `median`: 6 agent steps, 6 LLM calls,
3,597 prompt tokens total, peak context 1,802. The files are
Pier-compatible (Pier's own trajectory models validate Chimera output;
see `tests/atif/`).

## Reproduction

```bash
source .env   # GLM-5.1 via the Anthropic-compatible endpoint
chimera bench-compare \
    --agents react,plan-execute \
    --benchmark human-eval --dataset <pool.json> --limit 3 \
    --model glm-5.1 \
    --max-llm-calls 6 --max-tool-calls 15 --max-wall-clock 150 \
    --seed 0 --format markdown \
    --output matrix.html --emit-atif trajectories
```

The pool is any JSON list of `{"id", "prompt", "test"}` tasks whose
`test` is self-asserting Python (the HumanEval adapter executes
`agent_output + test` directly). Same seed, same pool, same budget →
the same matrix shape on rerun.

## Next

- Same matrix over DeepSWE tasks via the Harbor adapter +
  `docker_env_factory` (per-task images; verified end-to-end against a
  live daemon in `tests/eval/benchmarks/test_harbor_docker.py`).
- Larger pools where planning loops can earn their overhead back.

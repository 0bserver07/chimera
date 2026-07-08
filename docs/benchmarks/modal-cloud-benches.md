---
title: Benchmarks on Modal — the whole grid, parallel, in the cloud
description: Chimera runs any agent × any benchmark on Modal cloud sandboxes and GPUs — per-task sandboxes, whole-cell-in-cloud, and parallel grid fan-out. Live-proven on workspace 0bserver07 with glm-5.2[1m].
---

# Benchmarks on Modal

Chimera can run its agent × benchmark evaluations on [Modal](https://modal.com)
— cloud sandboxes, optional GPUs, and **parallel fan-out** so a whole
comparison grid runs concurrently instead of grinding serially on one machine.

There are three levels, each usable on its own.

## 1. Per-task Modal sandboxes — `bench-matrix --env modal`

Run the matrix locally but execute **each task in a fresh Modal cloud sandbox**,
optionally on a GPU:

```bash
chimera bench-matrix --agents react --benchmarks mbpp \
  --limit 5 --env modal --model "glm-5.2[1m]"

# on a GPU sandbox:
chimera bench-matrix --agents coding-agent --benchmarks mbpp \
  --limit 5 --env modal --modal-gpu T4
```

The orchestration and model calls stay local; the sandbox isolates task
execution + grading. Fails loudly if Modal auth is missing (never silently
local). **Live-proven:** `react × mbpp = 100%`; a real Tesla T4 provisions via
`--modal-gpu T4`.

## 2. Whole cell in the cloud — `scripts/modal_bench_app.py`

Run **everything** on Modal — orchestration + model inference (via the
`chimera-glm` secret) + execution + grading:

```bash
modal run scripts/modal_bench_app.py --agent react --bench mbpp --limit 2
```

Chimera's source and the staged datasets bake into the Modal image; the
`chimera-glm` secret supplies the model credentials so inference originates
*from* Modal. **Live-proven:** `react × mbpp n=2 = 2/2`.

## 3. Parallel grid fan-out — `::grid`

Fan an entire agents × benchmarks grid out as **concurrent Modal functions**.
Wall-clock ≈ the slowest single cell, not the serial sum — the fix for the
sequential-timeout problem that plagues single-machine depth runs.

```bash
modal run scripts/modal_bench_app.py::grid \
  --agents coding-agent,react,reflexion,tree-of-thought \
  --benches mbpp,livecodebench --limit 5 --model "glm-5.2[1m]"
```

Each cell runs in its own container; a failed cell surfaces as an `error`
without sinking the grid; results collect into an agents×benches table and
`data/modal-grid-<ts>.json`.

### First fan-out result

<!-- FILLED IN FROM data/modal-grid-<ts>.json WHEN THE PROOF RUN LANDS -->
_Proof run in progress: flagship + three loop architectures × {mbpp,
livecodebench} at n=5, fanned out concurrently on Modal. Table lands here with
the data file._

## What runs

- **Benchmarks** (staged, runnable): mbpp · humaneval-plus · mbpp-plus ·
  livecodebench · math500 · tau-bench · swe-bench.
- **Agents** (all 13): react · plan-execute · reflexion · tree-of-thought ·
  **coding-agent** (flagship) · full-tools · action-first · minimal · explore ·
  swebench · retry-min · lint-loop · plan-act.

## Setup

- Modal auth: `modal setup` (writes `~/.modal.toml`) or `MODAL_TOKEN_ID` /
  `MODAL_TOKEN_SECRET`.
- Model secret for cloud inference:
  `modal secret create chimera-glm ANTHROPIC_API_KEY=… ANTHROPIC_BASE_URL=… ANTHROPIC_MODEL=…`

Spec + implementation checkpoints: `docs/specs/modal-bench-fanout.md`.

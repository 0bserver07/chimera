---
title: Benchmark Matrix — progress & how to re-run
description: Live agent × benchmark status, the flagship depth scorecard, and the exact commands to reproduce or extend any column.
---

# Benchmark Matrix — progress

State as of master `6bb9917` (2026-07-10). Model `glm-5.2[1m]` via z.ai.
Every number cites a `data/*.json`; run `uv run python scripts/verify_status.py`
for the live 8-check state. Companion: [modal-cloud-benches](../benchmarks/modal-cloud-benches.md),
[playbook 13](../playbooks/13-live-bench-runs.md).

> **Display superseded by [the Observatory](../benchmarks/observatory.md)** —
> the public results page, generated from the `data/*.json` receipts by
> `scripts/render_observatory.py` (regenerate: `uv run python
> scripts/render_observatory.py`; freshness gate: `--check`). The tables below
> stay as the operational guide — how to re-run, extend, or troubleshoot any
> column — but cite the Observatory, not this page, for numbers.

## 1. Flagship depth scorecard — `coding-agent`, FULL datasets

The assembled `chimera code` stack over each benchmark's whole dataset, clean
grader. `EXACT` = `status_counts`-verified, zero infra errors.

| Benchmark | n | Score | Status | Source |
|---|---:|---:|---|---|
| mbpp-plus | 378 | **99.7%** (377) | ✅ exact | `fullscore3-*.json` |
| mbpp | 427 | **99.1%** (423) | ✅ exact | `fullscore2-*.json` |
| human-eval-plus | 164 | **92.1%** (151) | ✅ exact | `fullscore2-*.json` |
| math500 | 500 | **77.6%** (388) | ✅ ~exact (4 budget-exh) | `fullscore3-*.json` |
| livecodebench | 175 | ⊘ **RETRACTED** | ⊘ not a measurement* | `fullscore1-*.json` |

*livecodebench: the previously published ≥18.9% (33/175) is **withdrawn**. The
original caveat (175 slow tasks need ~14.5h vs a 12h cell timeout, so the column
could not complete) was real but not the main problem — the adapter grades 36% of
the dataset against the wrong contract, so no run of it produces a LiveCodeBench
score. Detail: 63 of the 175 staged tasks are `functional` + `starter_code` while the runner executes `python solution.py < stdin`, so 36% of the denominator cannot pass under any answer; the staged file is platform-blocked (AtCoder 0–111, LeetCode 112–174) so a contiguous `--limit` slice is single-platform; and only public sample tests are staged. A floor over a 36%-unpassable denominator is not a floor. See `docs/notes/bench-diagnosis-darklight1.md`.

## 2. Breadth matrix — 13 agents × 7 benches (n=1, `data/matrix-full-glm52.json`)

The instrument demonstration: every agent against every bench, one task each.
`✓` = solved, `·` = failed. Depth (n>1) has only been run for the flagship
above; a clean multi-agent depth matrix is the open item (§4).

| Agent | HE | HE+ | mbpp | mbpp+ | math | lcb | tau |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| react | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| plan-execute | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| reflexion | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| tree-of-thought | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **coding-agent** ★ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| full-tools | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| action-first | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| minimal | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| explore | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| swebench | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| retry-min | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| lint-loop | · | · | · | · | · | · | · |
| plan-act | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

12/13 solve 7/7 at n=1. `lint-loop` is the one honest 0 (writes no solution
file on from-scratch codegen — known agent-behavior gap, not a grading bug).

## 3. How to re-run — the exact commands

Always follow [playbook 13](../playbooks/13-live-bench-runs.md): verify → smoke
→ throttled/detached grid → integrity-scan.

```bash
# 0. state must be green first
uv run python scripts/verify_status.py

# 1. LOCAL — one agent × one bench (fast smoke, per-task exec on this machine)
set -a; source .env; set +a
uv run chimera bench-matrix --agents react --benchmarks mbpp \
  --limit 5 --model "glm-5.2[1m]"

# 2. MODAL — one cell in the cloud (per-task sandbox; add --modal-gpu T4 for GPU)
modal run scripts/modal_bench_app.py --agent coding-agent --bench mbpp --limit 5

# 3. MODAL DETACHED — a whole grid, durable, survives machine sleep
modal run --detach scripts/modal_bench_app.py::grid_detached \
  --run-id myrun --agents coding-agent,react,reflexion \
  --benches mbpp,human-eval-plus --limit 500
# ...then collect anytime (results persist to the chimera-bench-results Volume):
modal run scripts/modal_bench_app.py::collect --run-id myrun

# 4. SWE family — each instance in its official per-instance image on Modal
uv run chimera bench-matrix --agents react --benchmarks swe-bench-verified \
  --limit 5 --env swe-modal --model "glm-5.2[1m]"

# 5. stage a bench that isn't loaded yet
uv run chimera bench-fetch <name>          # e.g. bigcodebench, aimo
```

Knobs that matter (learned the hard way): concurrency is capped at 4
(`_MAX_CONCURRENCY` in `modal_bench_app.py`) because one model account can't
serve more; the cell timeout is 12h (`run_cell_durable`, raise for slow
columns); detached runs persist to a Volume so a sleeping laptop can't lose them.

## 4. Open items ("try these again")

- **livecodebench exact** — re-run at `n=50` (~4h, fits the 12h timeout) or bump
  `run_cell_durable` timeout past 15h.
- **Multi-agent DEPTH matrix** — the only depth data today is the flagship's 5
  columns. Fire a throttled detached grid (e.g. 4 agents × 3 benches × n=25) to
  fill real per-cell pass rates; earlier multi-agent grids predate the grading
  fixes and are NOT trustworthy.
- **SWE live grading** — faithful FAIL_TO_PASS grading is wired; a live scored
  run needs the multi-GB per-instance images pulled on Modal (`--env swe-modal`).
- **External-CLI fidelity rows** — needs the external tools installed.

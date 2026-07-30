---
title: "The Observatory — verified agent × benchmark results"
description: "Chimera's public scoreboard: every number generated from a committed data receipt, labeled EXACT or lower-bound from per-task status tallies, each with the exact command that reproduces it."
---

<!--
  GENERATED FILE — do not edit by hand; edits are overwritten.
  Source of truth: data/*.json receipts + scripts/render_observatory.py.

  Regenerate (rewrites this file AND the byte-identical site copy):
      uv run python scripts/render_observatory.py

  Freshness gate (exit 1 when this page is stale vs data/):
      uv run python scripts/render_observatory.py --check
-->

# The Observatory

Chimera's results page. Every number below was generated from a JSON receipt committed in `data/` by `scripts/render_observatory.py` — nothing is hand-typed. Each cell names its source file, carries an EXACT or lower-bound label derived from per-task status tallies, and sits next to the command that reproduces it. **Don't trust us — run the command.**

Companions in the repo: `docs/benchmarks/modal-cloud-benches.md` (how the runs execute) · `docs/progress/benchmark-matrix.md` (operations guide: how to re-run or extend any column) · `scripts/verify_status.py` (the live integrity audit).

## How to read these numbers

Every grid cell records a per-task terminal-status tally (`status_counts`, e.g. `{completed: 427}` or `{completed: 496, budget_exhausted: 4}`). That tally is what separates a score from a floor: a cell is labeled **EXACT** only when it shows every task completing cleanly — zero infrastructure errors hiding in the denominator.

When a cell mixes clean runs with infrastructure errors, its status is `partial_error` and every errored task counts as a **miss, never a pass**. The resulting number is a **lower bound** (written `≥`): the agent's true rate is at least that high, and the cell is not citable as a score. Runs that predate `status_counts` can only ever be lower bounds, however clean they look.

The graders are hardened so failure cannot masquerade as success: empty output grades False, wrong output grades False, and the checker is actually invoked — an earlier grader bug silently passed *any* HumanEval+ output, and every number measured under it was invalidated and re-measured rather than kept. The same discipline gates this very page: a receipt containing an `error`-status cell that claims passes **aborts generation**. So does a *uniform zero* — a cell of 5+ tasks that all reached `completed` yet passed none. That pattern has always been a broken grading contract rather than a measured 0%, so it must be diagnosed against a known-correct solution before anything ships.

## 1. Flagship full-dataset scorecard — `coding-agent`

The assembled `chimera code` stack (`coding-agent`) over each benchmark's **whole dataset**, hardened grader, model `glm-5.2[1m]`, executed on Modal cloud. One row per benchmark; the Basis column is derived from the receipt's per-task status tally, and the Source column names the exact receipt.

| Benchmark | n | Score | Basis | Source |
|---|---:|---|---|---|
| mbpp | 427 | **99.1%** (423/427) | EXACT — 0 errors (`{completed: 427}`) | `data/modal-grid-fullscore2-20260709-154339.json` |
| humaneval-plus | 164 | **92.1%** (151/164) † | EXACT — 0 errors (`{completed: 164}`) | `data/modal-grid-fullscore2-20260709-154339.json` |
| mbpp-plus | 378 | **84.9%** (321/378) †‡ | EXACT — 0 errors (`{completed: 378}`) | `data/modal-grid-fullscore4-20260730-plusgrade.json` |
| math500 | 500 | **77.6%** (388/500) | ~EXACT — `{completed: 496, budget_exhausted: 4}` (≤0.8% margin) | `data/modal-grid-fullscore3-20260709-234842.json` |
| livecodebench | 175 | ⊘ **RETRACTED** | see the retraction note below | `data/modal-grid-fullscore1-20260709-105308.json` |

⊘ **livecodebench** — **RETRACTED — still not a LiveCodeBench score.** The previously published ≥18.9% (33/175) and the later 88% (44/50) are both withdrawn. Three defects; **two are now fixed, the third is not, and it alone is disqualifying**.

*Fixed (2026-07-25):* 63 of the 175 tasks are `functional` + `starter_code` but were run as `python solution.py < stdin`, so 36% of the denominator could not pass under any answer — the adapter now grades them by calling the `Solution` method with decoded arguments. And the staged file is platform-blocked (AtCoder 0–111, LeetCode 112–174), so a contiguous `--limit` slice was single-platform; slicing is now stratified (`--limit 50` → 25 AtCoder + 25 LeetCode, was 50 + 0).

*Not fixed:* **only public sample tests are staged.** For 24 of 50 tasks in one slice, every graded assertion is printed in the prompt, so the number measures copying at least as much as solving. Private tests must be staged before any figure from this adapter is citable, regardless of how correct the grading contract now is. Diagnosis: `docs/notes/bench-diagnosis-darklight1.md`.

† **humaneval-plus** — 1 of 164 tasks (`HumanEval/32`) is unpassable by construction — its upstream EvalPlus assertion `_poly(*candidate(*inp), inp)` splats the float `find_zero` returns and dies of `TypeError` before comparing anything, so the achievable maximum is **163/164 = 99.4%**, not 100%.

† **mbpp-plus** — 1 of 378 tasks (task `590`, `polar_rect`) is unpassable under plus grading — the upstream expanded harness compares the returned floats with `atol=0`, and the dataset's own canonical answer differs from the expected value in the last ULP, so the achievable maximum is **377/378 = 99.7%**, not 100%. Read the score against that, not against a notional perfect run.

‡ **mbpp-plus** — **base-grading inflation, now measured.** The previously published **99.7% (377/378)** for this column was graded with the dataset's *base* `test_list` asserts — three or four per task against a suite designed for hundreds — because `MBPPPlus` inherited MBPP's grading path and never executed the EvalPlus expanded `test` blob staged in every row. That harness now runs. Re-running the **identical model, tasks and agent** under it gives **321/378 = 84.9%** (`data/modal-grid-fullscore4-20260730-plusgrade.json`), a **14.8-point** gap attributable to grading alone, since nothing else changed. The expanded contract is strictly stronger, not merely different: a hardcoded lookup satisfying every base assertion for `is_not_prime` is accepted by base and rejected by plus.

Receipts: 5 cells, **$22.85** total model spend (sum of the source cells' `cost_usd`).

**Reproduce:**

```bash
# small-n smoke of the same cells (any machine; model creds required;
# drop --limit to run each full dataset):
uvx --from chimera-run chimera bench-matrix --agents coding-agent \
  --benchmarks mbpp,humaneval-plus,mbpp-plus,math500 \
  --limit 5 --model "glm-5.2[1m]"

# how this data was actually produced — detached Modal grid
# (survives disconnects; cells persist to a Volume):
modal run --detach scripts/modal_bench_app.py::grid_detached \
  --run-id myscore --agents coding-agent \
  --benches mbpp,humaneval-plus,mbpp-plus,math500 --limit 500
modal run scripts/modal_bench_app.py::collect --run-id myscore
```

## 2. Depth matrix — 5 agents × 4 benchmarks, n=50 (`observatory1`)

**Run `observatory1` is in flight — no number appears here until its receipt lands.** The depth grid — agents `coding-agent,react,plan-execute,reflexion,tree-of-thought` × benchmarks `mbpp,humaneval-plus,mbpp-plus,math500` at n=50, model `glm-5.2[1m]` — is running detached on Modal; cells persist to the `chimera-bench-results` Volume as they finish. When `data/modal-grid-observatory1-<ts>.json` exists, this section regenerates from it:

```bash
modal run scripts/modal_bench_app.py::collect --run-id observatory1
uv run python scripts/render_observatory.py
```

**Reproduce:**

```bash
uvx --from chimera-run chimera bench-matrix \
  --agents coding-agent,react,plan-execute,reflexion,tree-of-thought \
  --benchmarks mbpp,humaneval-plus,mbpp-plus,math500 \
  --limit 50 --model "glm-5.2[1m]"

# or detached on Modal (how observatory1 runs):
modal run --detach scripts/modal_bench_app.py::grid_detached \
  --run-id observatory1 --agents coding-agent,react,plan-execute,reflexion,tree-of-thought \
  --benches mbpp,humaneval-plus,mbpp-plus,math500 --limit 50
modal run scripts/modal_bench_app.py::collect --run-id observatory1
```

## 3. Breadth grid — 13 agents × 7 benchmarks, n=1

The instrument demonstration: **every** replicated architecture against **every** staged benchmark, one task each, model `glm-5.2[1m]`, identical budget. n=1 proves the harness runs the full roster — it ranks nothing; depth (sections 1–2) is where ranking happens. `✓` = solved, `·` = failed.

| Agent | HE | HE+ | mbpp | mbpp+ | math | lcb | tau |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| **coding-agent** ★ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| react | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| plan-execute | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| reflexion | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| tree-of-thought | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| full-tools | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| action-first | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| minimal | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| explore | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| swebench | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| retry-min | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| lint-loop | · | · | · | · | · | · | · |
| plan-act | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Legend: HE=humaneval · HE+=humaneval-plus · mbpp+=mbpp-plus · math=math500 · lcb=livecodebench · tau=tau-bench.

12/13 agents solve 7/7 at n=1. A ✓ under `livecodebench` means only that the harness ran and the adapter's grader accepted an answer — that adapter is under retraction (see §1) and its ticks carry no benchmark claim. Ticks under `mbpp-plus` were graded by the weaker harness described in §1's ‡ note — this grid's receipts predate the stronger one, so a ✓ there means the base contract, not the column's name. `lint-loop`'s zero row is honest — it writes no solution file on from-scratch codegen (a known agent-behavior gap, not a grading bug).

Receipts: 91 cells, **$0.78** total model spend. Source: `data/matrix-full-glm52.json`.

**Reproduce:**

```bash
uvx --from chimera-run chimera bench-matrix \
  --agents coding-agent,react,plan-execute,reflexion,tree-of-thought,full-tools,action-first,minimal,explore,swebench,retry-min,lint-loop,plan-act \
  --benchmarks humaneval,humaneval-plus,mbpp,mbpp-plus,math500,livecodebench,tau-bench \
  --limit 1 --model "glm-5.2[1m]"
```

---

*Generated by `scripts/render_observatory.py` from 5 data receipts: `data/matrix-full-glm52.json` · `data/modal-grid-fullscore1-20260709-105308.json` · `data/modal-grid-fullscore2-20260709-154339.json` · `data/modal-grid-fullscore3-20260709-234842.json` · `data/modal-grid-fullscore4-20260730-plusgrade.json`.*

*Data date: 2026-07-30 (newest receipt's mtime — the generator never reads the wall clock, so regenerating over unchanged inputs is byte-identical).*

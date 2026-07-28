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

### First fan-out result (glm-5.2[1m], n=5, 2026-07-07)

Flagship + three loop architectures × {mbpp, livecodebench}, **8 cells fanned
out concurrently on Modal** (`data/modal-grid-20260707-201224.json`):

| Agent | LiveCodeBench | MBPP |
|---|---|---|
| **coding-agent** (flagship) | **5/5 (100%)** | **5/5 (100%)** |
| react | 4/5 (80%) | 5/5 (100%) |
| reflexion | 4/5 (80%) | 5/5 (100%) |
| tree-of-thought | 3/5 (60%) | 4/5 (80%) |

**8 cells, 0 errors, $0.83.** Two things this shows:

- **The flagship earns "premiere."** `coding-agent` is the *only* agent to go
  100% on both — and the gap is on the harder column (LiveCodeBench), exactly
  where the 24-tool assembled stack should pull ahead of a bare loop. At n=5
  this is a real signal, not the n=1 tie.
- **Parallel beats serial, and scales.** Wall-clock **611s** vs **782s
  sum-of-cells** even at 8 cells; the difference is fixed cold-start + image
  overhead. At the full 91-cell grid, serial ≈ hours while parallel stays ≈ the
  slowest single cell (~350s) — this is the fix for the single-machine
  depth-run timeouts.

### Flagship scorecard — `coding-agent` on Modal (glm-5.2[1m])

The flagship run across all five staged benchmarks, all executing on Modal:

| Benchmark | Result | n |
|---|---|---|
| human-eval-plus | ~~10/10~~ **INVALIDATED** → re-measured **5/5 (100%)** at n=5 | 10 → 5 |
| livecodebench | **9/10 (90%)** | 10 |
| math500 | 4/5 (80%) | 5 |
| mbpp | 6/10 (60%) | 10 |
| mbpp-plus | 5/10 (50%) | 10 |

**Integrity correction (`0275ec3`):** every human-eval-plus number graded
before that commit is **invalid** — the grader's `test` field only *defines*
`check(candidate)` and the adapter never appended the call, so `exec()` merely
defined a function and returned clean: **any output passed, including wrong
ones**. The 10/10 above was measured under that broken grader. Fixed (empty →
False, wrong → False, canonical → True, `check(entry_point)` now invoked) and
re-measured on Modal post-fix: the flagship scored **5/5 at n=5** on the honest
grader (`data/modal-grid-20260708-232643.json`). The other columns used
different graders and are unaffected. (math500 initially errored because the
Modal image lacked the datasets — a one-line image fix, `14c32b9`.)

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

## SWE-bench on Modal — per-instance images + faithful grading (2026-07-09)

- **`--env swe-modal`** (`8d57ea8`): each SWE-bench instance runs in ITS official
  evaluation image (`swebench/sweb.eval.x86_64.<instance_id>`) on Modal — no
  local docker. Live-proven: the image pulls, boots, and the instance's
  test-patch applies inside `/testbed` ($0.023, 55s;
  `data/swe-modal-smoke.json`, committed 2026-07-28 — it had been cited while
  untracked, so until then it resolved on one machine and nowhere else).

  **Read that receipt as infrastructure evidence only.** It records
  `passed: 1, total: 1` for one instance, and that 1/1 is exactly the vacuous
  pass described two bullets down: a pytest run that executed ZERO tests and
  graded clean because nothing failed. It is **not** a SWE-bench resolve, and
  a single instance would not be a resolve *rate* even if it were. What this
  run demonstrates is that the per-instance image pulls, boots, and accepts
  the test-patch; the cost and wall-clock are its only quotable numbers. The
  receipt is committed *because* it is the artifact that exposed the
  vacuous-pass bug — not because it scored anything.
- **Faithful grading** (`ad8842d`): `evaluate()` now runs the instance's named
  `FAIL_TO_PASS` + `PASS_TO_PASS` tests (pytest node ids, chunked, exit-code
  authoritative) — the official resolve criterion — with conda auto-activation
  keyed on the official image marker. The legacy blanket fallback remains for
  rows without test lists.
- **Vacuous-pass guard** (same commits): the live smoke exposed that a pytest
  run executing ZERO tests graded as a pass (`all_passed` = no failures).
  A result reporting zero-run counters now grades False — absence of failure
  is not success.

## Measurement-integrity hardening (2026-07-08/09)

The wide grids caught three real harness bugs, each now fixed + regression-
tested + enforced by `scripts/verify_status.py` (8 checks, offline, <90s):
errored/empty runs graded as passes (incl. a HumanEval+ checker that was never
invoked — all pre-`0275ec3` HumanEval+ numbers are invalid, see the corrected
scorecard above); cells labeled by their LAST task's status (`partial_error`
now reports mixes honestly, `a44a687`); and unthrottled fan-out flooding the
single model account (capped at 4, `c2e78f4`). Playbook:
`docs/playbooks/13-live-bench-runs.md`.

## Runnable set (2026-07-09)

11 distinct benchmarks / 3,678 tasks stage + load flag-free: humaneval-plus
164 · livecodebench 175 · math500 500 · mbpp 427 · mbpp-plus 378 · swe-bench
300 · human-eval 164 · bigcodebench 1,140 · humaneval-x 164 · aimo 90 ·
tau-bench 1 (dataset-capped; upstream tasks are code-defined, `d6e6dc6`).

## Flagship full-dataset scorecard — coding-agent, glm-5.2[1m] (2026-07-09)

**Four columns re-run clean at throttle-4 (`fullscore2` + `fullscore3`) are now
EXACT/near-exact and citable — `status_counts` verify the error split**, so
these are real pass rates, not the fullscore1 lower bounds:

| Benchmark (full n) | Score | Basis |
|---|---|---|
| **mbpp-plus** (378) | **99.7%** (377/378) | ✅ EXACT — 0 errors (`{'completed':378}`) |
| **mbpp** (427) | **99.1%** (423/427) | ✅ EXACT — 0 errors (`{'completed':427}`) |
| **human-eval-plus** (164) | **92.1%** (151/164) | ✅ EXACT — 0 errors (`{'completed':164}`) |
| **math500** (500) | **77.6%** (388/500) | ~EXACT — 496 clean + 4 budget_exhausted (≤0.8% margin) |
| livecodebench (175) | ⊘ **RETRACTED** | ⊘ **RETRACTED** — the adapter does not measure LiveCodeBench: 63 of the 175 staged tasks are `functional` + `starter_code` while the runner executes `python solution.py < stdin`, so 36% of the denominator cannot pass under any answer; the staged file is platform-blocked (AtCoder 0–111, LeetCode 112–174) so a contiguous `--limit` slice is single-platform; and only public sample tests are staged. A floor over a 36%-unpassable denominator is not a floor. See `docs/notes/bench-diagnosis-darklight1.md`. |

**This vindicates the integrity work spectacularly.** Every column fullscore1
rated as garbage was harness noise, not agent weakness — the clean re-runs
reveal the flagship is *strong*:

| Benchmark | fullscore1 (noisy) | clean re-run | delta |
|---|---|---|---|
| mbpp | ≥35.4% "DO NOT CITE" | **99.1%** | +64pt |
| math500 | ≥43.2% "investigate" | **77.6%** | +34pt |
| mbpp-plus | ≥91.0% | **99.7%** | +9pt |

The cost-proxy correctly flagged those columns as error-dominated; throttling to
4 and re-running clean recovers the true scores. Only livecodebench remains
(finishing in `fullscore3`) — expected low, but it will be exactly low. The
fullscore1 lower-bounds table is retained below
for provenance.

## fullscore1 — flagship full-dataset, all 5 columns (2026-07-09, LOWER BOUNDS)

Full-dataset columns on the trustworthy grader, run DETACHED overnight
(complete: `data/modal-grid-fullscore1-20260709-105308.json`). **All columns
are `partial_error` — errored tasks count as misses, so these are lower bounds,
NOT scores.** This run predates `status_counts`, so error share is inferred from
cost-per-task: errored tasks die cheap, so a column at full-work cost is mostly
real work, a half-cost column is error-dominated. Full-work ≈0.85–0.9¢ for
codegen/math; **LiveCodeBench is inherently pricier (contest-style, long
generations)** so its ¢/task is not comparable to the others.

| Column (full n) | Result (lower bound) | ¢/task | Read |
|---|---|---|---|
| mbpp-plus (378) | **≥ 91.0%** (344/378) | 0.89 | full-work cost — the one near-citable number |
| math500 (500) | ≥ 43.2% (216/500) | 0.85 | full-work cost; misses look real — investigate before citing |
| livecodebench (175) | ⊘ **RETRACTED** | 5.68 | the cost-share reasoning below was sound and still reached a wrong conclusion — spend proves the agent worked, never that the grader graded the right thing |
| mbpp (427) | ≥ 35.4% (151/427) | 0.41 | half-cost ⇒ likely error-dominated — DO NOT CITE |
| human-eval-plus (164) | ≥ 31.7% (52/164) | 0.39 | half-cost ⇒ likely error-dominated — DO NOT CITE |

**Verdict: not a publishable scorecard, but three of five are real signal.**
mbpp-plus ≥91% and math500 ≥43% ran at full-work cost. **The livecodebench
reasoning in this section is retained as a worked example of a plausible wrong
conclusion.** It argued that $9.93 of spend (5.68¢/task) proved the flagship did
substantial work on nearly every task, so the low score had to be a genuine
result rather than an artifact. The spend was real and the inference was still
wrong: cost measures effort expended, never whether the grader evaluated the
right contract. 36% of those tasks could not have passed no matter what the
agent wrote. Original text: LiveCodeBench is hard
contest codegen). The two half-cost columns (mbpp, human-eval-plus) are
error-dominated and must NOT be cited — they need a re-run now that cells carry
`status_counts` (exact failure-vs-error split, ~$3). math500's 43% is worth its
own investigation (MATH-500 easy-slice history was far higher on stronger
models — model + prompt-path sensitivity, not assumed harness fault).

---
title: "Claude-Models Code Benchmark Fan-out — 2026-05-26"
description: "HumanEval+ (Sonnet 4.6, Opus 4.7), LiveCodeBench (Haiku 4.5, Sonnet 4.6), and MATH-500 (Haiku 4.5, Sonnet 4.6) in a single parallel 4-task run."
---

# Claude-Models Code Benchmark Fan-out — 2026-05-26

**Date:** 2026-05-26
**Models:** Haiku 4.5, Sonnet 4.6, Opus 4.7
**Benchmarks:** HumanEval+ (164), LiveCodeBench easy-50, MATH-500 (500 / 200-problem)
**Raw data:** `data/humanevalplus-claude-*.json`, `data/livecodebench-claude-*.json`, `data/math500-claude-*.json`
**Commits:** `bc01996` (Sonnet HE+), `8dc18a2` (Opus HE+), `da6f6b5` (LCB Haiku+Sonnet), `eb80aae` (MATH-500 Haiku+Sonnet)

## Summary

Four parallel benchmark tasks fanned out from a single fan-out spec (a local 9-section handoff document, not committed to the repo). All four landed cleanly within ~95 minutes wall-clock.

| Task | Benchmark | Model | Result | Cost | Wall time | Commit |
|------|-----------|-------|--------|------|-----------|--------|
| A | HumanEval+ (164) | Sonnet 4.6 | **93.9%** (154/164) | $9.24 | 25 min | [`bc01996`](https://github.com/0bserver07/chimera/commit/bc01996) |
| B | HumanEval+ (164) | Opus 4.7 | **95.1%** (156/164) | $21.49 | 28 min | [`8dc18a2`](https://github.com/0bserver07/chimera/commit/8dc18a2) |
| C | MATH-500 full (500) | Haiku 4.5 | **89.2%** (446/500) | $1.47 | ~30 min | [`eb80aae`](https://github.com/0bserver07/chimera/commit/eb80aae) |
| C | MATH-500 first-200 (200) | Sonnet 4.6 | **91.5%** (183/200) | $13.04 | ~75 min | [`eb80aae`](https://github.com/0bserver07/chimera/commit/eb80aae) |
| D | LiveCodeBench easy-50 | Haiku 4.5 | ~~**98.0%** (49/50)~~ **[RETRACTED — see below]** | $0.18 | 3.7 min | [`da6f6b5`](https://github.com/0bserver07/chimera/commit/da6f6b5) |
| D | LiveCodeBench easy-50 | Sonnet 4.6 | ~~**98.0%** (49/50)~~ **[RETRACTED — see below]** | $2.87 | 8.2 min | [`da6f6b5`](https://github.com/0bserver07/chimera/commit/da6f6b5) |

**Total spend: $48.29** across all six runs — almost exactly the $48 a-priori envelope from the fan-out spec.

> **Correction, 2026-07-28 — the two Task D figures above are withdrawn. The
> other four rows stand.** `livecodebench` is in the `RETRACTED` registry in
> `scripts/render_observatory.py`, so the observatory on this repo's site
> publishes no LiveCodeBench score; these hand-written rows were still quoting
> one, which is precisely the gap a retraction that only reaches generated
> pages leaves behind.
>
> **What is not wrong here.** The runs happened and the receipts are committed:
> `data/livecodebench-claude-haiku-4-5-20251001-results.json` and
> `data/livecodebench-claude-sonnet-4-6-results.json` both record
> `passed: 49, total: 50`, and the costs match. The grader was not the broken
> one — this run's failure list names a real wrong answer per model. Two of the
> three defects behind the repo-wide retraction also do **not** apply: this
> runner deliberately *excluded* the LeetCode `functional` problems instead of
> mis-running them as stdin, so no part of its denominator was unpassable by
> construction.
>
> **Why the number still cannot stand.** What the receipts actually record is
> their own `split` field: `easy-stdin-first-50`. That is a single-difficulty,
> single-test-format, first-50 head slice with the LeetCode half of the dataset
> removed — not a sample of LiveCodeBench, and not a quantity the name
> "LiveCodeBench easy-50" lets a reader compare with anything. Separately, the
> repo can no longer vouch for the staged test coverage: the disqualifying
> defect in the retraction is that only public sample tests were staged, and
> the claim below that "all public + private tests must pass" has not been
> re-verified against the staged file.
>
> Struck through rather than deleted, per `docs/releases/0.9.1.md`: a dated
> report is a historical record, and silently editing a published number is the
> same class of dishonesty as publishing a wrong one. Full diagnosis:
> `docs/notes/bench-diagnosis-darklight1.md`.

## Shared setup

### Environment

Both calling paths sourced from one env file:

```bash
# /tmp/claude_oauth_env.sh
export ANTHROPIC_API_KEY="sk-ant-oat01-…"          # used by chimera's Python provider
export CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat01-…"    # used by the `claude` CLI
unset ANTHROPIC_BASE_URL
unset ANTHROPIC_MODEL
```

The same OAuth token is used by both vars. The two-var layout exists because of a CLI quirk surfaced during this run (see below).

### Auth fix: `humaneval_cli.py` env-strip (folded into commit `bc01996`)

The `claude -p` CLI, when `ANTHROPIC_API_KEY` is set in the parent env, sends the OAuth token in the `x-api-key` header rather than as `Authorization: Bearer …`. The server rejects this as "Invalid API key" even though the same token works via raw API with Bearer auth.

Fix: `examples/benchmarks/humaneval_cli.py` now strips `ANTHROPIC_API_KEY` from the subprocess env before invoking `claude`:

```python
child_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
result = subprocess.run([...], env=child_env, ...)
```

This makes the runner robust regardless of how the env is sourced. The same pattern must be copied into any future CLI-based runner (LCB and MATH-500 CLI variants both replicate it).

### Billing routes

| Route | Models | Notes |
|-------|--------|-------|
| Direct via chimera's `anthropic` provider (Bearer) | Haiku 4.5 | Provider auto-detects `sk-ant-oat01-*` prefix and switches from `x-api-key` to `Authorization: Bearer`. See `chimera/providers/anthropic.py:116`. |
| `claude -p --output-format=json` subprocess | Sonnet 4.6, Opus 4.7 | Required: direct `/v1/messages` returns persistent 429s for these models on a Max account. The CLI taps Max quota first, then spills into Additional Usage. Per-call overhead: ~$0.05 cache-creation tax (~13K-token CLI context loaded fresh per call). |

### Datasets

| Dataset | Source | Cached at | Notes |
|---------|--------|-----------|-------|
| HumanEval+ | [`evalplus/evalplus`](https://github.com/evalplus/evalplus) | `/tmp/humaneval-plus.jsonl` | Same 164 problems as canonical HumanEval; ~80× more tests per problem. |
| LiveCodeBench | [`livecodebench/code_generation_lite`](https://huggingface.co/datasets/livecodebench/code_generation_lite) | `/tmp/livecodebench.jsonl` | First 50 `difficulty=easy` stdin-typed problems. Codeforces + atcoder included; leetcode functional-test problems excluded (different test interface). |
| MATH-500 | [`HuggingFaceH4/MATH-500`](https://huggingface.co/datasets/HuggingFaceH4/MATH-500) | `/tmp/math500.jsonl` | Test split, 500 problems. Hendrycks MATH subset with difficulty levels 1–5. |

## Task A — HumanEval+ × Sonnet 4.6

**Result: 154/164 (93.9%) — $9.24 — 25 min — commit `bc01996`**

Runner: `examples/benchmarks/humaneval_cli.py --model claude-sonnet-4-6 --dataset /tmp/humaneval-plus.jsonl --count 164`. Each problem is one `claude -p --no-session-persistence` invocation with the EvalPlus prompt.

### Failures (10)

| Type | Count | Problem IDs |
|------|-------|-------------|
| Subprocess timeout (>10s) | 3 | HE/32, HE/39, HE/139 |
| Assertion fail on EvalPlus edge cases | 7 | HE/76, HE/91, HE/106, HE/132, HE/134, HE/151, HE/154 |

Pattern: the 7 assertion failures are all problems where the canonical HumanEval test set passed but EvalPlus's adversarial inputs (empty containers, extreme integers, unicode edge cases) tripped up the generated code. None were syntax / formatting failures.

## Task B — HumanEval+ × Opus 4.7

**Result: 156/164 (95.1%) — $21.49 — 28 min — commit `8dc18a2`**

Same runner as Task A; only `--model` differs. Opus burns ~2.3× the cost of Sonnet per call on identical input.

### Failures (8)

| Type | Count | Problem IDs |
|------|-------|-------------|
| TypeError (`*` after non-iterable) | 1 | HE/32 |
| Subprocess timeout (>10s) | 1 | HE/139 |
| Assertion fail on EvalPlus edge cases | 6 | HE/44, HE/76, HE/91, HE/124, HE/132, HE/154 |

Both Sonnet and Opus fail on **HE/32, HE/76, HE/91, HE/132, HE/139, HE/154** — a 6-problem shared-failure core, suggesting these are EvalPlus tests that catch a common Python idiom both models converge on.

### HumanEval vs HumanEval+ delta

Cross-referencing the 2026-05-20 HumanEval run (commit `a25bec0`):

| Model | HumanEval (164) | HumanEval+ (164) | Δ |
|-------|-----------------|------------------|---|
| GLM-5.1 | 92.7% (152/164) | 89.6% (147/164) | −3.1pt |
| Haiku 4.5 | 95.7% (157/164) | 92.1% (151/164) | −3.6pt |
| Sonnet 4.6 | 98.8% (162/164) | 93.9% (154/164) | −4.9pt |
| Opus 4.7 | 100.0% (164/164) | 95.1% (156/164) | −4.9pt |

EvalPlus consistently extracts 3–5pt on this model family. Larger models have more room to lose because their HumanEval scores are near the ceiling.

## Task D — LiveCodeBench × Haiku 4.5 + Sonnet 4.6

~~**Result: Haiku 49/50 (98.0%) — $0.18 — 3.7 min · Sonnet 49/50 (98.0%) — $2.87 — 8.2 min — commit `da6f6b5`**~~
**[RETRACTED — see the correction under the summary table.]** The costs and
wall-clocks stand; the scores are withdrawn as LiveCodeBench results.

Runner: `examples/benchmarks/livecodebench_sample.py` (new, 420 lines). Each problem runs the candidate Python program as a subprocess with the test's stdin; stdout is whitespace-normalized and compared to expected. All public + private tests must pass.

Sample: first 50 problems by `difficulty=easy` and stdin test format (filters out leetcode functional-test problems, which use a different harness). Codeforces and atcoder problems retained.

### Failures (1 each)

| Model | Problem | Cause |
|-------|---------|-------|
| Haiku 4.5 | `abc313_a` | `ValueError: max() iterable argument is empty` on test 3 (edge case with empty input list) |
| Sonnet 4.6 | `abc302_a` | Off-by-one: expected `499999999999999999`, got `500000000000000000` (integer overflow in a `n//2` rounding) |

~~Both models scoring 98% on the easy tier is consistent with the LiveCodeBench leaderboard, which puts top frontier models in the 90s on easy.~~ **[RETRACTED, 2026-07-28 — see the correction under the summary table.]** This sentence was the most misleading line in the report: it validated an in-house head slice against an external leaderboard, which tells a reader the two are comparable. They are not. The slice is easy-only, stdin-only, first-50, with the LeetCode half of the dataset excluded; agreeing with a leaderboard number computed over the whole benchmark is a coincidence of range, not corroboration. Worth running medium (50 problems) next as a follow-up — that's where the spread between models becomes legible.

## Task C — MATH-500 × Haiku 4.5 + Sonnet 4.6

**Result: Haiku 446/500 (89.2%) — $1.47 — ~30 min · Sonnet 183/200 (91.5%) — $13.04 — ~75 min — commit `eb80aae`**

Runner: new `examples/benchmarks/math500_full.py`. Each problem prompts the model to wrap its final answer in `\boxed{…}`; the grader extracts via a balanced-brace parser and compares to the canonical `answer` field after normalization.

### Grader normalization rules (28 self-tests cover these)

`\dfrac` vs `\frac`, single-digit `\frac43` vs `\frac{4}{3}`, `\sqrt2` vs `\sqrt{2}`, `\left(...)` markers, unicode `76°` vs `76^\circ`, `\text{cm}` / `\mbox{}` units, thin-space thousands separators (`10,\!080`), dollar prefixes, `x = 5` LHS strip. Falls back to numeric comparison when both sides parse as floats.

### By-level breakdown (Hendrycks MATH difficulty 1=easy → 5=hard)

| Level | Haiku 4.5 (full 500) | Sonnet 4.6 (200 sample) |
|-------|----------------------|-------------------------|
| L1 | 42/43 (97.7%) | 21/21 (100.0%) |
| L2 | 87/90 (96.7%) | 40/44 (90.9%) |
| L3 | 101/105 (96.2%) | 38/41 (92.7%) |
| L4 | 115/128 (89.8%) | 41/44 (93.2%) |
| L5 | 101/134 (75.4%) | 43/50 (86.0%) |
| **Total** | **446/500 (89.2%)** | **183/200 (91.5%)** |

Clean monotonic difficulty curve on Haiku (L1 → L5 drops 22pt). Sonnet's curve is flatter — the gap between Sonnet and Haiku is widest at L5 (10.6pt) and closes at L4 (Sonnet only +3pt).

### Sample-size note (Sonnet on 200, not 500)

The Sonnet run used the first 200 problems of the test split (21 / 44 / 41 / 44 / 50 across L1–L5) rather than the full 500. Reason: `claude -p` CLI overhead made full 500 cost-prohibitive within the run's wall-clock budget. The first-200 sample's per-level mix tracks the full set within ~4pt per level (it slightly under-weights L4–L5, 47.0% vs 52.4%), so the 91.5% is roughly comparable to a full run but likely a point or so optimistic. A future top-up to 500 can run incrementally.

### Backends

Single runner, `--backend` flag:

- `--backend python` — chimera's `anthropic` provider, used for Haiku ($1.47 total). Bearer auth via auto-detected OAuth token.
- `--backend cli` — `claude -p` subprocess, used for Sonnet ($13.04 total). Same env-strip pattern as `humaneval_cli.py` to keep `ANTHROPIC_API_KEY` from poisoning the CLI's auth header.

## Agent fan-out methodology

This run was orchestrated as four parallel subagents, each scoped to one benchmark task. The fan-out spec was a local, uncommitted handoff document (~250 lines covering shared context, env, guardrails, per-task TASK A/B/C/D briefs, and reporting format). Each subagent received the full spec plus a short task-specific brief naming its task letter and any newly-discovered gotchas (e.g. the env-strip fix above).

Coordination was file-based:

- **Non-overlapping outputs:** each task writes to a different `data/*.json` path
- **Doc row partitioning:** Tasks A and B share `docs/benchmarks/README.md` L2 (HumanEval+); C edits L5 (MATH-500); D edits L4 (LiveCodeBench). A and B were explicitly told to `git pull --rebase` before push to resolve potential L2 conflicts (in practice the second pusher merged cleanly).
- **Independent commits:** each subagent stages explicitly (`git add <file>` per file, never `-A`), runs the 7 trademark scrubs locally before push, then watches its own CI green.
- **Token economy:** all four share the same OAuth token via `/tmp/claude_oauth_env.sh`. No coordination needed since each task has its own model and its own quota bucket on Max.

The fan-out is **manual** (orchestrator dispatches four worker subagents, each one is autonomous after dispatch). It is not driven by chimera's in-tree `mink.team` orchestration, which is a separate work-in-progress.

## Reproduction

Each task is independently reproducible — each task's section above names its runner script and flags. Example for Task A:

```bash
cd /path/to/chimera
source /tmp/claude_oauth_env.sh  # OAUTH token, both env vars set
uv run python -u examples/benchmarks/humaneval_cli.py \
  --model claude-sonnet-4-6 \
  --dataset /tmp/humaneval-plus.jsonl \
  --count 164 \
  --output data/humanevalplus-claude-sonnet-4-6-results.json
```

## References

- [HumanEval+ (`evalplus/evalplus`)](https://github.com/evalplus/evalplus) — arXiv:2305.01210
- [LiveCodeBench](https://livecodebench.github.io/) — dataset [`livecodebench/code_generation_lite`](https://huggingface.co/datasets/livecodebench/code_generation_lite)
- [MATH-500](https://huggingface.co/datasets/HuggingFaceH4/MATH-500) — Hendrycks et al. (HuggingFace H4 release)
- Prior GLM-5.1 HumanEval report: [`2026-03-30-humaneval-glm51.md`](./2026-03-30-humaneval-glm51.md)
- 4-model HumanEval baseline (2026-05-20): commit `a25bec0`
- MBPP companion run (2026-05-20): data in commit `a25bec0`, CLI runner in `201de42`
- Topical adapter docs: [HumanEval+](./humaneval-plus.md), [LiveCodeBench](./livecodebench.md), [AIMO/MATH-500](./aimo.md)

## See also

- [HumanEval](./humaneval.md), [MBPP](./mbpp.md), [BigCodeBench](./bigcodebench.md) — adjacent code-generation benchmarks
- `chimera/providers/anthropic.py:116` — OAuth auto-detection
- `examples/benchmarks/humaneval_cli.py` — patched runner (env-strip)
- `examples/benchmarks/livecodebench_sample.py` — new runner from this run

---
title: "Benchmark Integration Candidates"
description: "External coding / SWE benchmarks worth wiring into Chimera's benchmark registry — what they are, whether they're public, and the cheapest honest integration path for each."
---

# Benchmark Integration Candidates

A living shortlist of **external** coding/SWE benchmarks we do not yet ship,
with an honest read on availability and the least-effort integration path
into Chimera's `Benchmark` registry (`chimera/eval/harness.py` →
`chimera/cli/main.py::_load_benchmark`).

Ground rules for this file:

- **Mark provenance.** A fact pulled from a public dataset repo or a raw
  file is tagged **verified**; a number from a marketing page or leaderboard
  is tagged **unverified** until we reproduce it.
- **Interop, not compete.** External bench + framework names are third-party
  interop targets (same convention as the eval adapters and
  `teammate_runner`), not codename↔brand identity claims.
- **Spec ≠ done.** A candidate listed here is a plan. Nothing is "supported"
  until an adapter runs and grades a real task on a live model.

Integration modes referenced below:

| Mode | When it fits | Machinery |
| --- | --- | --- |
| **Reuse `HarborBenchmark`** | dataset already ships as a Harbor task tree (`task.toml` + `instruction.md` + `tests/` per dir) | `chimera/eval/benchmarks/harbor.py` (already registered as `harbor`) |
| **New `Benchmark` adapter** | custom JSON/JSONL schema, deterministic grader | subclass `Benchmark` like `swe_bench.py` |
| **Native-harness runner (A4)** | the bench only grades through its *own* harness (agentic/LLM judge) | `NativeHarnessRunner` (greenfield — see [agent-benchmark-matrix spec](../specs/agent-benchmark-matrix.md)) + `registry.external.example.json` |
| **Not integrable** | dataset + harness are private | document as methodology reference only |

---

## 1. Senior SWE-Bench (Snorkel AI)

- **Site (leaderboard):** <https://senior-swe-bench.snorkel.ai/>
- **Dataset (public split):** <https://github.com/snorkel-ai/senior-swe-bench-v2026.06>
- **Version researched:** `v2026.06`, on `2026-07-03`.

### What it is

An agent benchmark that grades a coding agent as a **senior** engineer rather
than a junior one: under-specified instructions, multi-file / multi-stack
changes, and bug/perf tasks that require runtime investigation (logs,
profiling, reproduction). Tasks are mined from **real merged pull requests**
across production repositories.

**Verified** (from the public dataset repo):

- **50 public tasks**, one directory each under `tasks/`. Repos represented in
  the public split include PostHog, Gitea, Electric, Immich, Prefect,
  Teleport, Turborepo, Plausible, Firezone, Paperless-ngx, Better-Auth, and
  Harbor — 13 repos, tasks named `<repo>-<type>-<slug>` (e.g.
  `gitea-feat-fast-forward-only`, `posthog-feat-approval-gating`).
- Task types span `feat` / `fix` / `perf` / `refactor` / `add`; a
  `[metadata.taxonomy]` block tags each with `task_type`, `stack`
  (go, elixir, python, ts, rust, …), and a skills list.
- The repo description is literally *"Harbor dataset for Senior SWE-Bench
  (v2026.06)"* — it is distributed in the **Harbor task format**.
- **License: none declared** (the GitHub repo has a `null` license field).
  Treat as *look, don't vendor* — clone at eval time, do not copy tasks into
  Chimera.

**Unverified** (from the marketing site / leaderboard, reproduce before
quoting): a claimed ~50 additional *private* tasks; top solve rate ~24.0%
(Claude Opus 4.8); median instruction length ~31% of "SWE-Bench Pro"; feature
tasks averaging ~11 files and ~97–117K output tokens.

### Task & grading format

Each task directory is a standard Harbor layout (**verified** against
`tasks/gitea-feat-fast-forward-only/`):

```
<repo>-<type>-<slug>/
    task.toml          # version="1.0"; [environment] base_image, memory="8G",
                       # allow_internet; [verifier] timeout + [verifier.env];
                       # [agent] timeout; [metadata] family/variant/segment/
                       # tags + [metadata.origin] repo/base_commit/pr_numbers
                       # + [metadata.taxonomy] task_type/stack/skills
                       # + [metadata.oracle_scope] sloc/files/hunks
    instruction.md     # the prompt, shown to the agent verbatim
    environment/       # per-task Docker build context (image baked at base_commit)
    tests/             # the grading pipeline (see below)
    solution/          # held-out reference PR
```

**Grading is agentic, not a deterministic unit test.** The `tests/` dir ships
a multi-stage verifier — `run_verify.py` (runtime correctness),
`run_validate.py` (a *validation agent* that writes behavioural tests adapted
to the submitted diff — ~72 KB), `run_judge.py` (an LLM taste/quality judge —
~34 KB), and `run_aggregate.py` — all orchestrated by `tests/test.sh`. The
headline metric is a **"tasteful solve"**: functional pass **and**
validation-agent pass **and** quality thresholds (rubric / bloat / practice /
relative-taste). Consequently the grader:

- needs the task's prebuilt **Docker image** (repo baked at `base_commit`),
- needs `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `PORTKEY_API_KEY` and the
  `SSB_OVERRIDE_*` model env declared in `[verifier.env]`,
- **is itself LLM-driven** — it costs money per graded task and is not
  bit-reproducible.

Snorkel's own runner is Harbor:
`harbor run --repo snorkel-ai/senior-swe-bench-v2026.06 -a $AGENT -m $MODEL`.

### Recommended Chimera integration path

**Reuse `HarborBenchmark` + a thin profile subclass — the ingestion is
nearly free.** Chimera already ships `HarborBenchmark`
(`chimera/eval/benchmarks/harbor.py`, registered as `harbor`) and already
proved the format on the DeepSWE Harbor task set (README row A12). The public
Senior split matches that layout, so all 50 tasks discover/parse/prompt today.

Only two deltas need a profile shim (delivered as the scaffold below):

1. **`task.toml` key remap.** Senior uses `[environment].base_image` (base
   parser reads `docker_image`), `memory`/`storage` as `"8G"`/`"20G"` strings
   (base parser reads int `memory_mb`/`storage_mb`), and nests repo/commit
   under `[metadata.origin]` (base parser reads top-level `repository_url` /
   `base_commit_hash`). Missing keys are tolerated but land empty, so the
   subclass maps them explicitly — and surfaces the `taxonomy` / `oracle_scope`
   fields for matrix slicing.
2. **Grading fidelity.** `HarborBenchmark.evaluate()` runs `tests/test.sh` and
   treats exit 0 as pass — which *does* invoke the real Snorkel verify →
   validate → judge → aggregate pipeline **when the Docker image + API keys are
   present**. Whether `test.sh`'s exit code equals the published
   "tasteful-solve" threshold must be confirmed on a **live run** before any
   number is reported. If it doesn't, the honest fallback is the **native-
   harness runner (A4)**: drive `harbor run …`, parse its results (a
   `senior-swe-bench` entry in `registry.external.example.json`).

**Effort:**

- Task ingestion (discover / parse / prompt for all 50): **XS** — the scaffold
  subclass below; no new dependencies (stdlib `tomllib`).
- Faithful "tasteful-solve" grading: **M–L** — gated on live infra (per-task
  Docker images + judge/validation API keys + spend), not on code. Confirm the
  `test.sh` exit-code ≡ tasteful-solve equivalence, or add the A4 runner.

**Status:** scaffold written (`chimera/eval/benchmarks/senior_swe_bench.py`),
not registered in the CLI, no task run yet.

---

## 2. Cursor Evals — "CursorBench" (Cursor / Anysphere)

- **Page:** <https://cursor.com/evals>
- **Methodology post:** <https://cursor.com/blog/cursorbench>
- **All facts below are UNVERIFIED** — sourced from Cursor's own marketing /
  blog pages; there is no public dataset, harness, or paper to check against.

### What it is (unverified)

Cursor's **internal** evaluation suite (current production version referenced
as *CursorBench 3.1*) for measuring coding-agent performance on "ambiguous,
multi-file tasks from real Cursor sessions." Dimensions include solution
correctness, code quality, efficiency, and interaction behaviour; v3.1 adds
codebase-understanding, bug-finding, planning, and code-review problems on top
of v3.0's edit/refactor/bugfix set.

### Task & grading format (unverified)

- **Task sourcing:** mined from real Cursor sessions via an internal tool
  ("Cursor Blame") that traces committed code back to the agent request that
  produced it, yielding a query + ground-truth-solution pair. Many tasks come
  from Cursor's *own* codebase / controlled sources to limit training-data
  contamination; the suite is refreshed every few months.
- **Task shape:** intentionally short, under-specified, multi-file; scope is
  claimed to have "roughly doubled" from v3.0 to v3.1 and to exceed SWE-bench
  Verified / Pro / Multilingual in LOC and mean files touched (monorepos,
  production-log investigation, long-running experiments).
- **Grading:** "agentic graders" (LLM-judge style; no public rubric), plus
  both offline and online evaluation. Reported scores are percentages
  (observed range on the page ≈ 31.9%–72.9% across models); the page itself
  warns results are noisy and small deltas may not be significant.
- **Cost column:** avg cost/task = each model's published per-token pricing
  applied to the tokens it used, averaged across tasks.

### Recommended Chimera integration path

**Not integrable as a runnable benchmark — document as a methodology
reference only.** There is **no public dataset, no harness, no repo, no
paper** — only a hosted leaderboard and blog posts. Chimera cannot reproduce
CursorBench, and we do not invent a schema for a bench we can't see.

What *is* useful is the **methodology**, worth borrowing for Chimera's own
eval design (and consistent with the mission's comparative-methodology
framing):

- **Session-mined, provenance-linked tasks** ("Cursor Blame" = query ↔
  ground-truth commit) — a template for building *our own* underspecified,
  real-world task source instead of curated issues.
- **Agentic graders + offline/online split** — informs the `graders=` hook on
  `Harness` and the LLM-judge critics already in `chimera/critic/`.
- **Contamination control** via private/internal codebases — a caution for any
  bench we publish.

**Effort:** N/A (blocked on availability). Revisit only if Cursor releases a
dataset or harness. If we want this *capability*, it is a **new bench we
author**, not an integration — out of scope here.

**Status:** reference only; nothing to build.

---

## Also on the radar (already backlogged)

From the [agent × benchmark matrix spec](../specs/agent-benchmark-matrix.md)
(Axis B). These are tracked there; listed here so this file is the single
"what external bench next?" entry point.

| Benchmark | State in Chimera | Path | Notes |
| --- | --- | --- | --- |
| **SWE-bench Pro** | not present | new `Benchmark` adapter (B3) | `scaleapi/SWE-bench_Pro-os`. Harder, longer instructions; Senior-SWE-Bench's site benchmarks *against* it. |
| **Multi-SWE-bench** | **already shipped** | `multi_swe_bench.py` (5 language runners) | Not really a candidate — present. Live run vs upstream still TODO (needs per-language Docker images). |
| **R2E-Gym** | not present | Harbor-format feeder → reuse `HarborBenchmark` | `R2E-Gym/R2E-Gym`; home of DeepSWE. Task source / training env; same ingestion story as Senior SWE-Bench. |
| **SWE-Gym** | not present | Axis-B feeder (2,438 tasks) | `SWE-Gym/SWE-Gym`. Task source / training env. |

The recurring lesson: **any bench shipped in the Harbor task format is an
ingestion no-op for Chimera** (`HarborBenchmark` already handles it) — the only
real work is grading fidelity when the bench uses an agentic/LLM judge instead
of a deterministic test.

## See also

- [`docs/benchmarks/README.md`](./README.md) — the transparency framework
  (status of every bench we *do* run).
- [`docs/specs/agent-benchmark-matrix.md`](../specs/agent-benchmark-matrix.md)
  — the runner unification + Axis-B gaps.
- [`chimera/eval/benchmarks/harbor.py`](../../chimera/eval/benchmarks/harbor.py)
  — the Harbor-format adapter these candidates reuse.

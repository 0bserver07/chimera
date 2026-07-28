---
title: Capability Matrix — Agents × Benchmarks
description: The single consolidated inventory of every Chimera agent implementation and every benchmark integration, plus what is actually wired today versus designed.
---

# Capability Matrix — Agents × Benchmarks

This is the one page that crosses the two axes. Other docs enumerate **one** axis
each — [benchmarks](../benchmarks/README.md) (the bench side),
[coding-agents](../coding-agents.md) (the 7 codenames), the
[field guide](https://0bserver07.github.io/chimera/field-guide/) (external
reference agents), [feature-comparison](../research/feature-comparison.md)
(Chimera vs competitors), [tier-status](../tier-status.md) (feature maturity).
None of them crosses **agents × benchmarks**. This page does.

> **Why there is no single "N agents" number.** The agent axis is *layered*, and
> the layers compose (a codename runs a preset, which runs a loop, under a
> posture). Counting them as one flat number is misleading — the honest picture
> is the layers below. This is also why an earlier read of the repo kept
> "finding more": each layer is a different axis.

## Agent axis (all verified in-repo)

| Layer | Count | Members | Source |
|---|---:|---|---|
| Loop implementations | **8** | react · retry · plan_act · plan_execute · reflexion · tree_of_thought · lint_feedback · autonomous | `chimera/core/loops/` |
| Codename CLIs (postures) | **7** | mink · otter · ferret · weasel · shrew · stoat · badger | `chimera/{codename}/` |
| Assembly presets | **6** configs / **7** keys | coding_agent · codex · minimal · explore · kimi · swebench, plus `claude_code` — a 7th key whose config differs from `coding_agent` in the `name` field alone (verified by `dataclasses.asdict` diff) | `chimera/assembly/presets.py` |
| Built-in preset agents | **5** | build · explore · general · plan · review | `chimera/agents/presets/*.py` |
| Loop styles (distinct loops) | **4** | retry-min (retry) · react-full (react) · lint-loop (lint_feedback) · plan-act (plan_act); former ids swe-agent/codex/aider/cline are back-compat aliases | `chimera/agents/presets/agent_styles.py` |
| Subagent profiles | **4** | executor · planner · researcher · reviewer | `chimera/agents/presets/subagents/` |
| Composition patterns | **3** | pipeline · ensemble · supervisor | `chimera/composition/` |
| Loop postures (prompt) | **2** | plan · tdd | `LOOP_POSTURES` in `chimera/assembly/coding_agent.py` |
| Base | — | ReAct `Agent`, assembled `CodingAgent`, ACP external driver | `chimera/core/`, `chimera/acp/` |

Plus the **external-agent driving surfaces** (not internal implementations, but
things that can *be* an agent-under-test): ACP (`ACPClient`) and CLI-template
(`teammate_runner` — already drives `codex exec`, `opencode acp`).

And a **wrapping layer** on top of any agent — the **9 synthesis strategies**
(`chimera/training/strategies/`: convergence · tree_search · curriculum ·
ensemble · majority_voting · aimo_ensemble · passthrough · cegis · incremental).
These run an agent under a search/voting/curriculum policy, so each is another
distinct behavior a benchmark cell can measure.

### External reference agents (documented, some replicated internally)

The field guide covers **10** external agents. Those marked ✅ have a
code-backed internal replica (see the replica styles row above) and are the
[replica-vs-real](../specs/agent-benchmark-matrix.md#signature-experiment-replica-vs-real)
candidates:

aider ✅ · claude-code (≈`coding_agent`) · claw-code · cline ✅ · codex-cli ✅ ·
kimi-cli (≈`kimi` preset) · little-coder · opencode · pi · swe-agent ✅

## Benchmark axis

**31** distinct `Benchmark` subclasses ship behind one `Harness` — **27** in
`chimera/eval/benchmarks/` (every one registered in `_BENCHMARKS`, reachable as
`chimera bench <name>`) plus **4** in `chimera/shrew/benchmarks/` that are
*not* registered and are reachable only through `chimera shrew bench`. Fully
enumerated in [benchmarks/README](../benchmarks/README.md). Summary by family:

| Family | Count | Examples |
|---|---:|---|
| SWE / repo-fix | 10 | SWE-bench (+Verified), Multi-SWE-bench, SWE-PolyBench, SWE-Lancer, SWT-bench, FeatureBench, ClineBench, DPAIArena, Harbor |
| Code-gen | 8 | HumanEval (+Plus, +X), MBPP, BigCodeBench, LiveCodeBench, ProgramBench, Aider-Polyglot |
| Math | 2 | AIMO, MATH-500 |
| Agentic / web | 2 | τ-bench, WebArena |
| Long-context | 2 | NoCha, ContextBench |
| Shrew-side (unregistered) | 4 | GAIA, Terminal-Bench, HarborBench, Aider-Polyglot (a *second* class, distinct from the `eval` one) |
| Generic harness | 1 | Custom |

Reusable across every bench: **6 graders** (`chimera/eval/graders/`:
LLMRubric, FileExists, PatternMatch, TestPass, Schema, Composite) and the
**sandbox layer** (`chimera/env/`: local · docker · git · remote · cloud ·
modal · e2b · daytona · native · ssh · ssh-async — Chimera's SWE-ReX
equivalent; guide: `docs/guides/remote-and-cloud-environments.md`).

> **Two honest exceptions** (loading works; grading is limited):
> **SWE-Lancer** — ingestion + dollar-weighted scoring helpers are real, but
> `evaluate()` deliberately raises `NotImplementedError` (grading needs the
> upstream containerized Playwright harness; live integration is a follow-up).
> **LiveCodeBench** — only the `codegeneration` scenario grades; the other
> three upstream scenarios raise until their schemas are wired.

## What is actually wired today

The axes are pluggable; the wired *crossings* are narrower than the inventory.
This is the honest current state (verified in `chimera/cli/main.py`,
`chimera/{otter,shrew,ferret,stoat,badger}/`):

| Surface | Agents | Benches reachable | Status |
|---|---|---|---|
| `chimera bench` | `--agent react\|code` | **27 registered** (all built adapters, incl. senior-swe-bench; 27 unique classes behind 46 CLI aliases in `_BENCHMARKS`) | ✅ wired |
| `chimera bench-compare` | internal loop postures | any 1 registered bench | ✅ wired |
| `chimera otter bench` | otter | HumanEval, MBPP, τ-bench | ✅ wired |
| `chimera shrew bench` | shrew | aider-polyglot, GAIA, harbor, terminal-bench | ✅ wired |
| `chimera ferret\|stoat\|badger bench` | — | — | ⚠️ scaffold (exit 2) |
| `chimera bench-matrix` | agents from the runner registry (internal roster + external via `--registry`) | any registered benches (N×M) | ✅ **shipped** — live-verified on glm-5.2[1m] (`data/matrix-full-glm52.json`, 91 cells) |
| `chimera bench-fidelity` | replica vs real (e.g. `full-tools` vs `codex-cli`) | any registered benches | ⚠️ **code shipped, never run** — see below |

**Takeaway:** the many-to-many runner **shipped**. `chimera bench-matrix` crosses
any set of registry agents against any set of registered benches under one
budget/sandbox/grader (the [agent-benchmark-matrix spec](../specs/agent-benchmark-matrix.md)),
and it is live-verified on glm-5.2[1m] by a committed receipt.

`chimera bench-fidelity` is the exception, and the earlier wording here
("both live-verified on glm-5.2[1m]") was wrong. The CLI and the scoring
harness exist, but **no fidelity pair has ever been run**: the tracker's
`T2.1` (install one external CLI) and `T2.2` (run `bench-fidelity` replica vs
real) are both still unchecked in
[agent-benchmark-matrix.tasks](../specs/agent-benchmark-matrix.tasks.md), and
`data/` holds no fidelity receipt. Fidelity is *plumbing complete, evidence
zero*.

What remains is therefore breadth **and** the first external run: staging
datasets for the benches that need them, installing the external agent CLIs
behind the registry entries, and enforcing per-agent budgets so multi-step
loops stay bounded.

---

# Verification axis — can I trust this cell?

Everything above this line is an **availability** claim: a class exists, a name
is registered, a CLI flag is accepted. Availability is a *code* fact and it is
cheap to check. Whether a thing has ever been **verified** — a known-correct
answer scored, a real model ran, a cloud sandbox actually booted — is a
*receipt* fact, and it is a different question with a different answer.

This repo has conflated the two at real cost. `humaneval-x` once returned a
live `0/50` with `status_counts {completed: 50}` — every task ran cleanly, none
passed, and the true answer was `50/50`. A LiveCodeBench column shipped as a
"documented lower bound" over a denominator 36% of which could not pass under
any answer. Both looked exactly like verified cells. So: **a row that says
"supported" without naming a receipt is the failure mode this section exists to
kill.**

## Verdict vocabulary

Only these words are used below. "Available", "supported", and a bare checkmark
are deliberately absent.

| Verdict | Means |
|---|---|
| **verified** | A receipt exists and is named. Grader proven against a known-correct answer *and* a real run is committed in `data/`. |
| **grader-verified** | The known-correct-answer canary passes, but no live agent run exists. The number would be trustworthy; there is no number. |
| **live-only** | A real run is committed, but the grader has never been proven against a reference answer. A number exists and nothing has checked it can be right. |
| **unverified** | Neither. The code exists. That is the whole claim. |
| **RETRACTED** | A number was published and is withdrawn. No figure from this adapter is citable. |
| **unverifiable-by-construction** | Cannot ever have a live receipt (a fictional provider, a user-supplied task set). |

`EXEMPT` in canary output means **unverified, not healthy.** It records that
an adapter *cannot be canaried offline* (its grading needs a checked-out repo,
a browser, a simulator). It is a reason, never a pass.

## How every number in this section was measured

Nothing here is quoted from memory or from another doc — CLAUDE.md warns
explicitly that counts drift. Re-run these and the numbers regenerate.

```bash
# Registered adapters: alias keys vs unique classes
uv run python -c "from chimera.cli.main import _BENCHMARKS; \
  print(len(_BENCHMARKS), len(set(_BENCHMARKS.values())))"          # -> 46 27

# Dataset staging: fetch specs, and what is staged on THIS machine
uv run python -c "from chimera.eval import datasets as d; \
  print(len(d.FETCHES), len(d.available()))"                        # -> 13 13

# Grader verification (the receipt-generator for this whole section)
uv run python scripts/canary_benchmarks.py --limit 25 --json
uv run --with numpy python scripts/canary_benchmarks.py --bench humaneval-plus --limit 40

# Agent roster and its aliases
uv run python -c "from chimera.eval.runners.registry import load_registry, \
  default_agent_specs; print(len(default_agent_specs()), len(load_registry()))" # -> 13 18

# Environment backends actually reachable through the factory
uv run python -c "from chimera.env.factory import _BUILTIN; print(sorted(_BUILTIN))"
```

**All six commands above were executed and reproduced their stated output** on
the date below — none is quoted from a prior run or from another document. A
block of commands that does not reproduce is worse than no block, because it
*looks* checked. The canary tallies are stable across sample size: `--limit 10`
and `--limit 25` both return `6 pass · 0 BROKEN · 0 unclassified · 1
env-missing · 1 not staged · 19 exempt`, so the cheap run is enough to confirm
this section.

Measured on 2026-07-28, branch `integrate/m-track`, CPython 3.12.8, under the
documented setup `uv sync --extra dev --extra anthropic`. Dataset staging is
**machine-local** (`~/.chimera/datasets`), so the "Dataset" column records what
a fetch spec *can* stage, and flags where this machine differs.

**The canary cannot fully run under the repo's own documented setup.** Two of
its eight recipes are blocked by dependencies no extra declares: `humaneval-plus`
needs `numpy` (declared nowhere in `pyproject.toml`, hence `ENV-MISSING`), and
`math-500` needs the `datasets` package, which appears only inside the heavy
`function_synthesis_compile` extra (torch + transformers + peft) and has no
`FetchSpec` at all. Both were verified absent after a full `--extra dev
--extra anthropic` sync. The `humaneval-plus` row below therefore reports a
run made with `uv run --with numpy`.

## Benchmarks — 27 registered

Canary column is from `scripts/canary_benchmarks.py --limit 25` (plus a
numpy-equipped re-run for `humaneval-plus`). Live-run column names a file in
`data/` or says none. Summary of that run: **0 BROKEN · 0 unclassified · 6 PASS
· 1 ENV-MISSING · 1 NOT-STAGED · 19 EXEMPT**.

| Benchmark | Registered | Dataset | Canary | Live-run receipt | Verdict |
|---|:-:|---|---|---|---|
| human-eval | ✅ | fetch + staged | PASS (25) | `data/humaneval-*-results.json`, `matrix-full-glm52.json` | **verified** |
| humaneval-plus | ✅ | fetch + staged | PASS (39) — needs `numpy`, else ENV-MISSING | `modal-grid-fullscore2-*.json` (164, EXACT) | **verified** · ceiling 99.4% |
| humaneval-x | ✅ | fetch + staged | PASS (25) | `modal-grid-hexfix1-20260724-231500.json` (50/50) | **verified** |
| mbpp | ✅ | fetch + staged | PASS (25) | `modal-grid-fullscore2-*.json` (427, EXACT) | **verified** |
| mbpp-plus | ✅ | fetch + staged | PASS (25) | `modal-grid-fullscore3-*.json` (378, EXACT) | **verified at BASE strength only** — see known-bad |
| aimo | ✅ | fetch + staged | PASS (25) | none | **grader-verified** |
| bigcodebench | ✅ | fetch + staged | PASS (17 of 25; 8 skipped — need matplotlib, numpy, pandas, psutil, seaborn) | none | **grader-verified (partial)** |
| math-500 | ✅ | **no fetch spec** — needs the `datasets` package or `--dataset`; staged inside the Modal image, not on this machine | NOT-STAGED here (a recipe exists) | `math500-*-results.json`, `modal-grid-fullscore3-*.json` (500) | **live-only** |
| livecodebench | ✅ | fetch + staged | EXEMPT — dataset stages no canonical solution | `modal-grid-fullscore1-*.json` | ⊘ **RETRACTED** |
| tau-bench | ✅ | 1 bundled task only | EXEMPT — agentic, replays against a sim | `matrix-full-glm52.json` (n=1 cells) | **live-only**, n=1 |
| swe-bench | ✅ | fetch + staged | EXEMPT — gold patch needs a repo + test runner | `data/swebench-lite-glm51-results.jsonl` (2/20 RESOLVED), `swebench-coding-agent-results.jsonl` (0/3) | **live-only** |
| swe-bench-verified | ✅ | fetch + staged | EXEMPT — same | none | **unverified** (runs today) |
| swe-polybench | ✅ | fetch + staged | EXEMPT — same | none | **unverified** (runs today) |
| swt-bench | ✅ | fetch + staged | EXEMPT — same | none | **unverified** (runs today) |
| multi-swe-bench | ✅ | fetch + staged | EXEMPT — same | none | **unverified** (runs today) |
| senior-swe-bench | ✅ | not staged | EXEMPT — same | none | **unverified** |
| swe-lancer | ✅ | not staged | EXEMPT — same | none | **unverified** — `evaluate()` raises `NotImplementedError` |
| aider-polyglot | ✅ | not staged | EXEMPT — needs a checked-out workspace | none | **unverified** |
| cline-bench | ✅ | not staged | EXEMPT — same | none | **unverified** |
| feature-bench | ✅ | not staged | EXEMPT — same | none | **unverified** |
| harbor | ✅ | not staged | EXEMPT — delegating adapter | none | **unverified** |
| programbench | ✅ | not staged | EXEMPT — submission-contract bench | none | **unverified** |
| dpai-arena | ✅ | not staged | EXEMPT — agentic arena | none | **unverified** |
| context-bench | ✅ | not staged | EXEMPT — agentic retrieval | none | **unverified** |
| nocha | ✅ | not staged | EXEMPT — long-context QA | none | **unverified** |
| webarena | ✅ | not staged | EXEMPT — needs a live browser | none | **unverified** |
| custom | ✅ | user-supplied | EXEMPT | n/a | **unverifiable-by-construction** |

**Headline arithmetic**, each figure from the commands above:

- **27** registered adapters (46 CLI alias keys → 27 unique classes).
- **13** have a `FetchSpec`; **14** load at least one task with no flags on this
  machine (the 13 staged, plus `tau-bench`'s single bundled task).
- **8** have a canary recipe at all; **7** are grader-verified (the 8th,
  `math-500`, has a recipe but no fetch spec here).
- **9** have any live-run receipt in `data/` — human-eval, humaneval-plus,
  humaneval-x, mbpp, mbpp-plus, math-500, livecodebench (retracted), tau-bench,
  swe-bench.
- **5** are fully **verified** (grader proven *and* a committed run):
  human-eval, humaneval-plus, humaneval-x, mbpp, mbpp-plus — and one of those
  five is base-graded.

So: **20 of 27 registered adapters have never had their grader checked** — the
19 `EXEMPT` plus `math-500`, whose recipe cannot run without a staged dataset.
**7 of those 20 load tasks and will produce a number today**: `swe-bench`,
`swe-bench-verified`, `swe-polybench`, `swt-bench`, `multi-swe-bench`,
`livecodebench`, and `tau-bench` (1 bundled task). Those seven are the
dangerous ones — from the CLI they are indistinguishable from a working column.

## Benchmarks reachable outside `_BENCHMARKS`

Terminal-Bench is the known case; it is not the only one. These four `Benchmark`
subclasses live in `chimera/shrew/benchmarks/`, are **absent from
`_BENCHMARKS`**, are never touched by the canary (which iterates `_BENCHMARKS`),
and have no dataset staging path in `chimera/eval/datasets.py`.

| Benchmark | Where | Registered | Dataset | Canary | Live-run receipt | Verdict |
|---|---|:-:|---|:-:|---|---|
| Terminal-Bench | `chimera/shrew/benchmarks/terminal_bench.py` + the TB agent adapter `chimera/benchmarks/terminal_bench_agent.py` | ❌ | hand-authored `tasks.json`; upstream deliberately not vendored | ❌ | **none** — a 30% figure is published, see known-bad | **unverified** |
| GAIA | `chimera/shrew/benchmarks/gaia.py` | ❌ | manual | ❌ | none | **unverified** |
| HarborBench | `chimera/shrew/benchmarks/harbor.py` | ❌ | manual | ❌ | none | **unverified** |
| Aider-Polyglot (shrew copy) | `chimera/shrew/benchmarks/aider_polyglot.py` | ❌ | manual | ❌ | none | **unverified** — a second class duplicating the registered `eval` one |

`chimera/benchmarks/` contains **zero** `Benchmark` subclasses: its
`terminal_bench_agent.py` is a `terminal_bench.BaseAgent` subclass, i.e. an
adapter that runs *inside* upstream Terminal-Bench, not a Chimera benchmark.

## Known-bad / retracted — read before citing any number

**1. LiveCodeBench — RETRACTED. No figure from this adapter is citable.**
Both the `≥18.9%` (33/175) and the later `88%` (44/50) are withdrawn. Registry:
`RETRACTED` in `scripts/render_observatory.py`. Two of three defects are fixed
(functional tasks are now called rather than piped; `--limit` slicing is
stratified across the platform-blocked file). The third is not and alone
disqualifies: **only public sample tests are staged**, so for 24 of 50 tasks in
one slice every graded assertion is printed in the prompt and the number
measures copying at least as much as solving. Diagnosis:
`docs/notes/bench-diagnosis-darklight1.md`. A "lower bound" over a denominator
that cannot pass is fiction with an inequality sign in front of it.

**2. mbpp-plus 99.7% is graded at BASE strength.** `MBPPPlus` subclasses `MBPP`
and inherits its `evaluate()`, which runs the dataset's original `test_list`
asserts. The EvalPlus expanded `test` harness is staged verbatim in every row
and **never executed** — stated in the `MBPPPlus` docstring
(`chimera/eval/benchmarks/mbpp.py`). The canary cannot catch this class: a
weaker suite still passes correct answers and rejects wrong ones. Read the
column as *MBPP+ tasks, base-graded*. The caveat is carried on the observatory;
it is **not** carried in `README.md`, which publishes "mbpp-plus 99.7%" with
zero mention of base strength (`grep -c "base-strength" README.md` → 0).

**3. humaneval-plus is capped at 99.4%, not 100%.** `HumanEval/32` is
unpassable by construction: the upstream EvalPlus assertion
`_poly(*candidate(*inp), inp)` splats the float `find_zero` returns and dies of
`TypeError` before comparing anything. Present verbatim in the raw HF rows, so
it is not a staging artifact. Disclosed by the canary as an exclusion
(`KNOWN_UNPASSABLE`), and confirmed by running
`--bench humaneval-plus --limit 40` (39 checked, 1 excluded).

**4. Terminal-Bench 30% has no receipt and three incompatible attributions.**
The number is published in four places that disagree about what produced it:
`docs/benchmarks/README.md` marks it `UNVERIFIED` and attributes it to
"TB's `terminus-1` agent, **not Chimera**"; `docs/benchmarks/2026-03-30-terminal-bench-glm5.md`
shows a `tb run --agent-import-path chimera.benchmarks.terminal_bench_agent:ChimeraAgent`
command and a comparison row reading "**Chimera (GLM-5.0) 30%**";
`docs/mink/benchmarks.md` (and its site copy) mark it **`validated`**;
`docs/tier-status.md` sources it as `MEMORY:`. The report itself states "Raw
data: Not saved" and "The 30% figure and 10-task count are from the benchmark
transparency framework (`docs/benchmarks/README.md`)" — which links back to the
report. **The citation is circular and terminates in no receipt.** The report
also records that the adapter bypasses Chimera's loop, tools, permissions,
events and detection entirely, so whatever was measured was not the agent
stack.

**5. Six receipt filenames are cited in docs but do not exist in `data/`.**
Reproduce with a filename-existence sweep over `docs/`, `site/`, `README.md`,
`CHANGELOG.md`:

| Missing file | Cited in | What it is claimed to prove |
|---|---|---|
| `data/depth-lcb-coding-agent-glm52.json` | `site/src/content/docs/guides/coding-agent.md` | LiveCodeBench **84% (21/25)**, "the best of any agent" — a **retracted** benchmark, published live on the site |
| `data/swe-modal-smoke.json` | `docs/benchmarks/modal-cloud-benches.md`, `docs/specs/agent-benchmark-matrix.tasks.md` | that `--env swe-modal` live-boots an official SWE-bench image |
| `data/modal-grid-20260708-232643.json` | `docs/benchmarks/modal-cloud-benches.md` | the human-eval-plus re-measurement after the grader-integrity fix |
| `data/modal-grid-observatory1-20260723-234334.json` | `docs/notes/bench-diagnosis-darklight1.md` | the `observatory1` depth grid |
| `data/modal-grid-darklight1-20260724-195209.json` | `docs/notes/bench-diagnosis-darklight1.md` | the darklight1 diagnosis run |
| `data/programbench-glm-5.2-code-results.json` | `docs/specs/modal-amd64-programbench-grading.md` | a ProgramBench result |

Plus one extension mismatch: `README.md`, `docs/benchmarks/2026-03-30-swebench-lite-glm51.md`
and `docs/mink/benchmarks.md` cite `data/swebench-lite-glm51-results.json`; the
file is `.jsonl`.

**6. `README.md` cites the retired receipt for its HumanEval headline.** The
table publishes "HumanEval 92.7% (152/164)" sourced to
`data/humaneval-glm51-results.json`. That file contains **109/164 = 66.5%** —
the harness-bug run the very next line says was retired. The 92.7% lives in the
near-identically named `data/humaneval-glm-5.1-results.json` (hyphenated). A
reader who follows the citation to check the headline finds the withdrawn
number instead.

**7. The "first multi-agent depth matrix" has no receipt.** `README.md`,
`CHANGELOG.md` and `docs/releases/0.9.2.md` describe it as shipped with
specifics (4 architectures × 4 benchmarks at n=50, 16 cells, $4.34). The
repo's own generated results page refuses to render it —
`docs/benchmarks/observatory.md` §2 reads "**Run `observatory1` is in flight —
no number appears here until its receipt lands**" — and
`docs/progress/benchmark-matrix.md` calls a clean multi-agent depth matrix "the
open item". No `data/modal-grid-observatory1-*.json` exists.

## Agents — which have ever driven a real benchmark run

The layer counts in the agent axis above are verified in-repo. What follows is
the orthogonal question: which of them has a committed run.

| Layer | Count | Live-run receipt | Verdict |
|---|---:|---|---|
| bench-matrix runner roster (`in-process`) | **13** ids (18 registry keys incl. 5 back-compat aliases) | **all 13** appear in `data/matrix-full-glm52.json`; 5 also in the Modal grids; `coding-agent` in 8 receipts | **verified** |
| Runner kinds | **4** (`in-process`, `acp`, `cli-template`, `native-harness`) | only `in-process` | the other **3 are unverified** — no external agent has ever been run |
| Loop implementations | **8** | 4 (`react`, `plan-execute`, `reflexion`, `tree-of-thought`) run as roster ids | 4 unverified against benchmarks |
| Loop styles | **4** distinct (8 `AgentPreset` constants → 4 names + 4 aliases) | 3 exposed as roster ids (`retry-min`, `lint-loop`, `plan-act`); `react-full` deliberately not re-exposed | 3 verified, 1 unexposed |
| Assembly presets | **6** configs | 6 reachable as roster ids | **verified** at n=1 |
| Codename CLIs | **7** | **none** — no `chimera otter bench` / `chimera shrew bench` receipt in `data/` | **unverified** |
| Built-in preset agents | **5** | none | **unverified** as benchmark runners |
| Subagent profiles | **4** | none | **unverified** |
| Composition patterns | **3** | none | **unverified** |
| Synthesis strategies | **9** | none | **unverified** |

Two honesty notes the runner itself enforces: `InProcessRunner` flags budgeted
*style* cells `budget_honored=False` rather than pretend to enforce a budget it
cannot inject, and `lint-loop`'s all-zero row in the breadth grid is a real
agent-behavior gap (it writes no solution file on from-scratch codegen), not a
grading bug.

## Providers / models

**11** `Provider` subclasses across **23** modules in `chimera/providers/`.
Availability here is near-total; live evidence is concentrated in two vendors.

| Provider | Class | Live-run evidence | Verdict |
|---|---|---|---|
| Anthropic | `anthropic:AnthropicProvider` | `data/humaneval-claude-*`, `mbpp-claude-*`, `math500-claude-*`, `humanevalplus-claude-*` — Haiku 4.5 / Sonnet 4.6 / Opus 4.7, full datasets with `total_cost_usd` | **verified** |
| OpenAI-compatible | `compatible:OpenAICompatibleProvider` | every glm-5.2[1m] receipt (`matrix-full-glm52.json`, all `modal-grid-*`) | **verified** |
| Modal Endpoints | `modal_endpoint:ModalEndpointProvider` | `CHANGELOG.md` says "live-smoked against a real endpoint (`9037bba`)", but the driver `scripts/modal_endpoint_smoke.py` is **manual, never run by CI/tests** (its own docstring) and writes no file — **no receipt in `data/`** | **unverified** — a narrated smoke, not a receipt |
| Modal (sandbox-side) | `modal:ModalProvider` | no `data/` receipt distinct from the sandbox runs | **unverified** |
| OpenAI | `openai:OpenAIProvider`, `openai_responses:OpenAIResponsesProvider` | none in `data/` | **unverified** |
| Google | `google:GoogleProvider` | none in `data/` | **unverified** |
| Ollama | `ollama:OllamaProvider` | narrative only (ProgramBench bridge); no `data/` receipt | **unverified** |
| Proxy | `proxy:ProxyProvider` | none | **unverified** |
| Cached | `cached:CachedProvider` | wrapper, not a backend | n/a |
| Faux | `faux:FauxProvider` | test double for `chimera.testing` | n/a |
| ACME Cloud | `acmecloud` (no subclass — a capability row + registry lambda) | **"ACME Cloud is not a real service"** per its own module docstring | **unverifiable-by-construction** |
| xAI / Grok | `xai` (factory over `OpenAICompatibleProvider`) | none in `data/` | **unverified** |

The capability matrix (`chimera/providers/capabilities.py`) is *data*, which is
its strength and its verification hazard: a new backend is a ~20-line row, and a
row that has never been dialed looks exactly like one that has.

## Environments / sandboxes

`create_environment` accepts **10** keys: `cloud`, `daytona`, `docker`, `e2b`,
`git`, `local`, `modal`, `remote`, `ssh`, `ssh-async`. `bench-matrix --env`
accepts a **narrower six**: `local`, `none`, `modal`, `swe-modal`, `e2b`,
`daytona` — docker, ssh, remote, cloud and git are **not** reachable from the
matrix runner.

`native` is listed in the sandbox layer above but is **not** in the factory
registry, and `NativeSandbox` is not an `Environment` subclass — it is a
standalone OS-confinement helper (`chimera/env/native_sandbox.py`).

The three Modal claims are three different claims and must not be merged:

| Claim | What it means | Receipt | Verdict |
|---|---|---|---|
| **Modal sandbox** (`--env modal`) | each task in a fresh Modal container | `data/modal-grid-*.json` (7 files, incl. full-dataset `fullscore*` runs) | **verified** |
| **Modal whole-cell** (`scripts/modal_bench_app.py`) | orchestration + inference + grading all on Modal | same `modal-grid-*` receipts | **verified** |
| **Modal GPU** (`--modal-gpu T4`) | a real GPU provisions for the sandbox | narrative only in `docs/benchmarks/modal-cloud-benches.md`; **no `data/` receipt names a GPU** | **unverified** |
| **`--env swe-modal`** | official per-instance SWE-bench image boots on Modal | cited as `data/swe-modal-smoke.json` — **file absent** | **unverified** |
| **Modal Endpoints** (model serving) | a served model answers over HTTP | smoke driver exists; no receipt | **unverified** |

| Backend | Extra / SDK | Creds | Live receipt | Verdict |
|---|---|---|---|---|
| local, git | none | none | every non-cloud run | **verified** |
| modal | `modal-sandbox` | `~/.modal.toml` or `MODAL_TOKEN_*` | `data/modal-grid-*.json` | **verified** (sandbox tier only) |
| docker | docker daemon | none | no `data/` receipt; `tests/integration/test_env_docker_integration.py` is excluded from the standard gate | **unverified** |
| e2b | `e2b` SDK | `E2B_API_KEY` | none — tests inject a fake at `chimera.env.e2b.Sandbox` | **unverified** |
| daytona | `daytona` SDK | Daytona creds | none — tests inject a fake at `chimera.env.daytona._sdk` | **unverified against the live service** |
| ssh / ssh-async | stdlib / `ssh` extra (asyncssh) | host creds | none — `tests/env/test_ssh_live.py` is excluded from the standard gate | **unverified** |
| remote, cloud | `httpx` | server URL | none | **unverified** |
| native (`NativeSandbox`) | none | none | none; **not factory-registered** | **unverified** |

Cloud backends do fail loudly rather than degrade to local (missing SDK →
`ImportError`, missing creds → `ValueError`, `bench-matrix --env` exits 2), so
an unverified backend cannot silently produce a fake cloud result. That is a
real safeguard — it is not a substitute for a receipt.

## Gap list — ranked by risk of being mistaken for verified

Ranked by *how confidently a reader would trust the cell*, not by effort.

1. **LiveCodeBench 84% on the public site.**
   `site/src/content/docs/guides/coding-agent.md` publishes "84% on
   LiveCodeBench code-generation — 21 of 25 tasks passed, the best of any agent
   on that benchmark", sourced to a receipt that does not exist, for the one
   adapter the repo has formally **RETRACTED**, with no caveat — while the
   observatory page on the *same site* carries the retraction. Every safeguard
   the repo built is bypassed because this page is hand-written and site-only
   (no `docs/guides/coding-agent.md` counterpart; `scripts/sync_docs.sh` mirrors
   only `docs/otter/` and `docs/mink/`).
   *Cheapest fix:* delete the depth paragraph or replace it with the retraction
   note. Zero compute.

2. **The five staged SWE-family graders that have never been checked.**
   `swe-bench`, `swe-bench-verified`, `swe-polybench`, `swt-bench`,
   `multi-swe-bench` all stage, all run, all emit a resolve rate today, and not
   one has scored a known-correct answer. A published `10% (2/20)` already rests
   on this. The failure mode is documented and *has already happened here*: a
   pytest run executing zero tests once graded as a pass.
   *Cheapest fix:* extend `RECIPES` with a gold-patch canary for one instance —
   apply the instance's own patch, run its `FAIL_TO_PASS`, require True and
   require the empty patch to be False. One instance, one container.

3. **Terminal-Bench 30%.** No receipt anywhere, circular citation, and the same
   number is labelled `validated`, `UNVERIFIED`, `MEMORY:` and
   "not Chimera" in four different files.
   *Cheapest fix:* pick one attribution and mark the rest retired; the figure
   cannot be reconstructed because the raw data was never saved.

4. **`README.md`'s HumanEval 92.7% citing the 66.5% receipt.** The most-read
   surface in the repo, one hyphen away from correct.
   *Cheapest fix:* change the filename to `data/humaneval-glm-5.1-results.json`.

5. **The depth matrix published without a receipt.** README/CHANGELOG/release
   notes assert it; the observatory refuses to render it; no receipt file
   exists.
   *Cheapest fix:* collect the run (`modal run …::collect --run-id observatory1`)
   or strike the claim until it lands.

6. **`mbpp-plus 99.7%` in `README.md` without the base-grading caveat.** The
   number is real for what executed; the name promises more.
   *Cheapest fix:* copy the observatory's ‡ footnote into the README line.

7. **`--env swe-modal` and `--modal-gpu` described as live-proven.** Both are
   narrative-only; the one named receipt is missing.
   *Cheapest fix:* re-run the documented smoke and commit the JSON, or downgrade
   the wording to "implemented, receipt pending".

8. **Daytona, E2B, docker, ssh, remote, cloud.** Six backends whose only
   coverage is injected fakes or gate-excluded live files. Lowest risk of the
   set — the docs are already honest that they are credential-gated — but a
   reader sees six equal-looking rows in a factory registry.
   *Cheapest fix:* one smoke per backend committed as `data/<backend>-smoke.json`,
   in the shape the missing `swe-modal-smoke.json` was supposed to have.

9. **`bench-fidelity` — plumbing complete, evidence zero.** No external CLI has
   ever been installed and no fidelity pair has ever been scored (tracker
   `T2.1`/`T2.2` both unchecked), yet replica-vs-real is the framework's
   headline comparative claim.
   *Cheapest fix:* install one external CLI and run one pair on `human-eval` at
   `--limit 5`.

10. **The canary's own blind spots.** Two of eight recipes cannot execute after
    the documented `uv sync --extra dev --extra anthropic`, so a contributor
    running the guarded command sees `ENV-MISSING` / `NOT-STAGED` and may read
    them as noise. Lowest risk on this list, cheapest fix on this list.
    *Cheapest fix:* declare `numpy` in a `bench` extra and give `math-500` a
    `FetchSpec` so `chimera bench-fetch math-500` works like the other twelve.

## See also

- [benchmark canary](../guides/benchmark-canary.md) — how the grader check works and why `EXEMPT` is not a pass.
- [observatory](../benchmarks/observatory.md) — the generated results page; every rendered number names its receipt.
- [benchmarks/README](../benchmarks/README.md) — the benchmark axis, with results, methodology, and gaps.
- [coding-agents](../coding-agents.md) — the 7 codenames, when to pick which.
- [agent-benchmark-matrix spec](../specs/agent-benchmark-matrix.md) — the many-to-many design + replica-vs-real experiment.
- [feature-comparison](../research/feature-comparison.md) — Chimera vs 8 external agents.
- [specs/README](../specs/README.md) — every design spec and its status.

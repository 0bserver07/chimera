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
| Assembly presets | **6** | coding_agent (=claude_code) · codex · minimal · explore · kimi · swebench | `chimera/assembly/presets.py` |
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

**29** benchmark adapters behind one `Harness`, fully enumerated in
[benchmarks/README](../benchmarks/README.md). Summary by family:

| Family | Count | Examples |
|---|---:|---|
| SWE / repo-fix | 10 | SWE-bench (+Verified), Multi-SWE-bench, SWE-PolyBench, SWE-Lancer, SWT-bench, FeatureBench, ClineBench, DPAIArena, Harbor |
| Code-gen | 8 | HumanEval (+Plus, +X), MBPP, BigCodeBench, LiveCodeBench, ProgramBench, Aider-Polyglot |
| Math | 2 | AIMO, MATH-500 |
| Agentic / web | 2 | τ-bench, WebArena |
| Long-context | 2 | NoCha, ContextBench |
| Shrew-side | 3 | GAIA, Terminal-Bench, HarborBench |
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
| `chimera bench` | `--agent react\|code` | **26 registered** (all built adapters, incl. senior-swe-bench — see the tasks backlog) | ✅ wired |
| `chimera bench-compare` | internal loop postures | any 1 registered bench | ✅ wired |
| `chimera otter bench` | otter | HumanEval, MBPP, τ-bench | ✅ wired |
| `chimera shrew bench` | shrew | aider-polyglot, GAIA, harbor, terminal-bench | ✅ wired |
| `chimera ferret\|stoat\|badger bench` | — | — | ⚠️ scaffold (exit 2) |
| `chimera bench-matrix` | agents from the runner registry (internal roster + external via `--registry`) | any registered benches (N×M) | ✅ **shipped** — live-verified on glm-5.2[1m] |
| `chimera bench-fidelity` | replica vs real (e.g. `full-tools` vs `codex-cli`) | any registered benches | ✅ **shipped** |

**Takeaway:** the many-to-many runner **shipped**. `chimera bench-matrix` crosses
any set of registry agents against any set of registered benches under one
budget/sandbox/grader (the [agent-benchmark-matrix spec](../specs/agent-benchmark-matrix.md)),
and `chimera bench-fidelity` scores replica-vs-real pairs — both live-verified on
glm-5.2[1m]. What remains is *breadth, not plumbing*: staging datasets for the
benches that need them, installing the external agent CLIs behind the registry
entries, and enforcing per-agent budgets so multi-step loops stay bounded.

## See also

- [benchmarks/README](../benchmarks/README.md) — the benchmark axis, with results, methodology, and gaps.
- [coding-agents](../coding-agents.md) — the 7 codenames, when to pick which.
- [agent-benchmark-matrix spec](../specs/agent-benchmark-matrix.md) — the many-to-many design + replica-vs-real experiment.
- [feature-comparison](../research/feature-comparison.md) — Chimera vs 8 external agents.
- [specs/README](../specs/README.md) — every design spec and its status.

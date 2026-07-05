---
title: Agent × Benchmark Matrix — Task Backlog
description: Living checklist companion to the agent-benchmark-matrix spec. Tracks the four buckets from design → wiring → integration → net-new build.
---

# Agent × Benchmark Matrix — Task Backlog

Companion to [agent-benchmark-matrix](agent-benchmark-matrix.md) and
[capability-matrix](../reference/capability-matrix.md). Bucket labels: **[S]**
spec→implement · **[W]** wiring · **[I]** integration · **[N]** net-new build.

**Legend:** ✅ done · 🟡 in progress · ⬜ todo

---

## Build status — 2026-07-03 (team build + live verification)

All runner + matrix **code** is built, tested, and **live-verified on `glm-5.2[1m]`**
(via the z.ai endpoint): `chimera bench-matrix --agents react --benchmarks human-eval
--model glm-5.2[1m]` ran end-to-end and scored **100% (1/1)**. Shipped: the
signature-aware loader, 25-benchmark wiring, `AgentRunner`+`InProcessRunner`, the
`AgentSpec` registry, the `chimera bench-matrix` CLI, the ACP / CLI-template /
native-harness runners, and the replica-vs-real fidelity harness — plus a required
provider fix (`fix(anthropic)`: an explicit client timeout so the SDK's
non-streaming ">10 min" guard no longer blocks glm/kimi's 32k-output eval calls).

**Still deferred (needs live external infra, not code):** running the external
native-harness fleet (mini-swe-agent/Agentless/…), the official SWE-bench grader,
published multi-cell matrices, and live replica-vs-real fidelity runs. These are
the ⬜ items below marked "Acceptance" / Phase 2–3 live steps.

---

## Phase 0 — foundation (internal agents × full bench registry)

- ✅ **[W] Signature-aware `_load_benchmark`** — inspect each ctor; map the
  dataset arg to `dataset_path`/`problems_path`/`dataset_dir` as declared; pass
  `limit` only when accepted. Fixes math500/aimo (`problems_path`) and
  context_bench (no `limit`). → `chimera/cli/main.py`.
- ✅ **[W] Wire all 15 built adapters into `chimera bench`** — registry
  **10 → 25** distinct (42 keys incl. aliases). Verified 25/25 load +
  instantiate. → `chimera/cli/main.py`.
- ✅ **[S/N] `AgentRunner` protocol + `AgentRunResult`** — the one contract every
  runner satisfies. → `chimera/eval/runners/base.py`.
- ✅ **[W] `InProcessRunner`** — wraps a ready agent or `agent_factory(provider)`
  (ComparativeEval's contract), maps native `AgentResult` → `AgentRunResult`.
  Unit-tested without an LLM. → `chimera/eval/runners/in_process.py`,
  `tests/eval/runners/test_in_process.py`.
- ⬜ **[S/N] `AgentSpec` + `matrix.yaml` registry loader** (project > user >
  builtin) — enumerate the internal roster (7 codenames + 6 presets + 4 styles +
  4 subagents) as runner specs; resolve `--agents` names. *(task #6)*
- ⬜ **[S/N] `chimera bench matrix` CLI + `MatrixReport`** — N agents × M benches
  under one BudgetSpec / sandbox / grader; one ATIF trajectory per cell.
  Generalizes `ComparativeEval` (1 bench × N loops) to 2D. *(task #7)*
- ⬜ **[N] Acceptance:** first internal-only 2×2 matrix on GLM-5 with per-cell
  ATIF.

## Phase 1 — external agents (ACP + CLI), no new benches

- ⬜ **[W] `ACPRunner`** — lift from `chimera/acp/client.py`. *(task #8)*
- ⬜ **[W] `CliTemplateRunner`** — lift the `teammate_runner` template pattern
  (`{prompt_file}`/`{repo}`/`{patch_out}`). *(task #8)*
- ⬜ **[I] Acceptance:** codex (cli) or opencode (acp) completes one SWE-bench
  Lite task in docker, graded identically to internal agents.
- ⬜ **[W] 4 more loops into `bench-compare`** — retry, plan_act, lint_feedback,
  autonomous already exist in `chimera/core/loops/`; only react/plan-execute/
  reflexion/tot are wired at `chimera/cli/bench_compare.py:29`.

## Phase 2 — native-harness fleet

- ⬜ **[N] `NativeHarnessRunner`** — run a framework's own SWE-bench harness →
  collect `predictions.jsonl`. *(task #9)*
- ⬜ **[N] Official SWE-bench harness grader** — controlled grader per column.
  *(task #9)*
- ⬜ **[I] Registry entries (docs, not framework code):** mini-swe-agent (first),
  agentless (cost baseline), aider, openhands, autocoderover, moatless, open-swe.
- ⬜ **[N] Budget-parity honesty flags** — per-cell flag when only wall-clock/cost
  were honored (never a silent "controlled" claim). *(task #10)*

## Phase 3 — external bench axis + published matrices

- ⬜ **[I] SWE-bench Pro adapter** (scaleapi/SWE-bench_Pro-os).
- ⬜ **[W] SWE-bench Full** — extend `SWEBench`.
- ⬜ **[I] R2E-Gym / SWE-Gym task sources** — via [harbor-task-adapter](harbor-task-adapter.md).
- ⬜ **[N] Replica-vs-real fidelity table** — pair replica (swe_agent/codex/aider/
  cline) vs the real CLI; Δpass-rate + trajectory divergence via badger parity.
  *(task #10)*
- ⬜ **[N] Publish** a ~4-agents × ~3-benches matrix on GLM-5; ATIF → Pier;
  export to SWE-bench/experiments.

## Cleanup

- ⬜ **[W] ferret/stoat/badger `bench`** — delegate to the canonical harness
  instead of the current exit-2 scaffolds.

---

## Wired benchmarks (Phase 0 result — 25 distinct)

`aider-polyglot · aimo · bigcodebench · cline-bench · context-bench · custom ·
dpai-arena · feature-bench · harbor · human-eval · humaneval-plus · humaneval-x ·
livecodebench · math500 · mbpp · multi-swe-bench · nocha · programbench ·
swe-bench · swe-bench-verified · swe-lancer · swe-polybench · swt-bench ·
tau-bench · webarena`

Note: these adapters are *reachable*; most still refuse to vendor upstream
datasets and expect a locally-staged `--dataset`. "Wired" ≠ "has a published
result" — see [benchmarks/README](../benchmarks/README.md) for run status.

## Post-audit follow-ups (2026-07-05)

- ⬜ **[N] Env-based grading path for file-artifact agents** — lint-loop (and any
  editor-loop agent) writes its artifact to the env; answer-graded benches score
  its final message (lint commentary) as 0%. Grade the env (run tests / harvest
  files) when the runner signals a file artifact. This is spec open question #1.
- ⬜ **[W] CodingAgentAdapter final-message extraction** — the assembled presets
  (coding-agent / full-tools / action-first / swebench) still score 0% on
  answer-graded benches even with the answer contract; their `_last_assistant_text`
  harvesting needs its own diagnostic + fix.
- ✅ **[N] FINAL_ANSWER_CONTRACT** — uniform final-answer prompt suffix in
  `run_matrix` (default on, controlled). Live-proven: reflexion × HumanEval
  0% → 100%.
- ✅ **[S] Grading-honesty docs** — swe-lancer (evaluate raises) + livecodebench
  (codegeneration only) called out in capability-matrix + ecosystem.
- ✅ **[S] Adapter test coverage 10 → 0 uncovered** — every benchmark adapter now
  has a unit test file.
- ⬜ **[N] Per-agent budget enforcement** — unchanged, still the top blocker for
  large live grids.

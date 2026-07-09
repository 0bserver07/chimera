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
- ✅ **[S/N] `AgentSpec` + registry loader** (JSON, not YAML — zero-dep) — (project > user >
  builtin) — enumerate the internal roster (7 codenames + 6 presets + 4 styles +
  4 subagents) as runner specs; resolve `--agents` names. *(task #6)*
- ✅ **[S/N] `chimera bench-matrix` CLI + `MatrixReport`** — — N agents × M benches
  under one BudgetSpec / sandbox / grader; one ATIF trajectory per cell.
  Generalizes `ComparativeEval` (1 bench × N loops) to 2D. *(task #7)*
- ✅ **[N] Acceptance (exceeded):** THE FULL GRID — 13 agents × 7 benches, 91 live
  cells on glm-5.2[1m] (`docs/benchmarks/2026-07-06-full-grid-glm52.md`).
  ⬜ per-cell ATIF emission is still owed (grid ran without trajectories).

## Phase 1 — external agents (ACP + CLI), no new benches

- ✅ **[W] `ACPRunner`** — `chimera/eval/runners/acp.py` (construct-verified).
- ✅ **[W] `CliTemplateRunner`** — `chimera/eval/runners/cli_template.py`.
- ⬜ **[I] Acceptance:** codex (cli) or opencode (acp) completes one SWE-bench
  Lite task in docker, graded identically to internal agents.
- ⬜ **[W] 4 more loops into `bench-compare`** — retry, plan_act, lint_feedback,
  autonomous already exist in `chimera/core/loops/`; only react/plan-execute/
  reflexion/tot are wired at `chimera/cli/bench_compare.py:29`.

## Phase 2 — native-harness fleet

- ✅ **[N] `NativeHarnessRunner`** — `chimera/eval/runners/native_harness.py`
  (unit-tested; live fleet runs still gated on installed tools).
- ⬜ **[N] Official SWE-bench harness grader** — controlled grader per column.
  *(task #9)*
- ⬜ **[I] Registry entries (docs, not framework code):** mini-swe-agent (first),
  agentless (cost baseline), aider, openhands, autocoderover, moatless, open-swe.
- ✅ **[N] Budget-parity honesty flags** — `MatrixCell.budget_honored/_note`.

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

- ✅ **[N] Env-artifact harvesting** (`harvest_env_artifacts`, default on in
  `run_matrix`) — fenceless answers get agent-written `.py` files appended as
  fenced blocks before grading; `raw["harvested_files"]` records it. Live-proven:
  harvest OFF 0% → ON 100% (full-tools, contract off). Spec open question #1.
- ✅ **[W] CodingAgentAdapter final-message extraction** — root cause: the
  adapter preferred a stale pre-tool message over the stream's terminal answer;
  stream is now authoritative. Live: full-tools × HumanEval 0% → 100%.
- ⬜ **[N] lint-loop write path (agent-side)** — corrected finding: on HumanEval
  lint-loop writes NO files (lint-fixates on the empty workspace), so its 0% is
  agent behavior. Needs a write tool / prompt in `agent_styles.py`, not harness work.
- ⬜ **[W] Preset budget parity** — expose `max_turns` (from `budget.max_llm_calls`)
  on `CodingAgentAdapter` so assembled presets move from cost-only caps toward
  fuller budget honoring.
- ✅ **[N] FINAL_ANSWER_CONTRACT** — uniform final-answer prompt suffix in
  `run_matrix` (default on, controlled). Live-proven: reflexion × HumanEval
  0% → 100%.
- ✅ **[S] Grading-honesty docs** — swe-lancer (evaluate raises) + livecodebench
  (codegeneration only) called out in capability-matrix + ecosystem.
- ✅ **[S] Adapter test coverage 10 → 0 uncovered** — every benchmark adapter now
  has a unit test file.
- ✅ **[N] Per-agent budget enforcement** — BudgetEnforcer through LoopConfig at the
  tool-executor choke point; live-proven `budget_exhausted` cells in the grid.


---

## THE CURRENT GAP LIST (2026-07-06) — executable, ranked

Reconciled against master `8d2a17b` (full grid + provider P0 wave). Effort tags
[S]/[M]/[L]. Cost bases are **extrapolated from the committed grid** —
`data/matrix-full-glm52.json`: 91 cells = $0.78 (~$0.0086/cell at n=1); per-row
costs there ground each estimate. Every ⬜ is a real, checkable step.

### Track 1 — Depth runs (turn the instrument into comparisons) — cheapest headline
- [ ] **T1.1 [S] τ-bench depth, n=25.** `chimera bench-matrix --agents react,plan-execute,plan-act,tree-of-thought --benchmarks tau-bench --limit 25 --model glm-5.2[1m] --max-tool-calls 20 --max-cost 0.15`. *Accept:* per-agent pass% at n≥25, `budget_exhausted` counted, JSON saved. *Cost basis:* ~4 agents × 25 ≈ 100 runs; tau rows ran $0.02–0.09 ⇒ ~$2–4.
- [ ] **T1.2 [S] LiveCodeBench depth, n=25** (react vs reflexion vs tree-of-thought — the codegen contrast). *Accept:* same. *Cost:* ~$1–2.
- [ ] **T1.3 [S] MBPP+ depth, n=25**, full 13-agent roster. *Accept:* same. *Cost:* ~$3–5.
- [ ] **T1.4 [S] Publish** `docs/benchmarks/2026-07-NN-depth-glm52.md` — real spread + variance, not uniform 100s; commit data JSONs `-f`.

### Track 2 — External rows live (first replica-vs-real fidelity number)
- [ ] **T2.1 [M] Install one external CLI** from `docs/examples/agent-registry.example.json`; document the install in the entry.
- [ ] **T2.2 [M] Run `chimera bench-fidelity`** pairing its replica style vs the real CLI on one bench. *Accept:* a fidelity delta printed + saved.
- [ ] **T2.3 [L] Phase-1 acceptance:** one external agent through **one SWE-bench Lite task in docker** end-to-end. *Accept:* graded predictions.jsonl for 1 task.
- [ ] **T2.4 [S] Publish** the first fidelity pair write-up.

### Track 3 — P1/P2 steal-backlog (from [pi-gap-analysis](../research/pi-gap-analysis.md) §3/§6)
- [x] **T3.1 [M] In-process hook API on the loop** — DONE (`36b19fd`). Audit
  found pre/post-tool already fired; added pre/post-turn + `emitter.on()/off()`
  (callbacks get full HookInput, can veto). 22 tests.
- [ ] **T3.2 [L] UI / extension registration surface** — third-party panels/commands.
- [x] **T3.3 [M] Hot-reload** of agents/plugins without restart — DONE (`0f4316c`).
  `PluginManager.reload()` re-imports the module (importlib.reload for sys.path
  plugins; loader re-exec fallback for file/dir-loaded) + re-activates; proven
  v1→v2 source pickup.
- [ ] **T3.4 [M–L] Orchestrator-style daemon** over `--mode rpc` (long-lived multi-session server).
- [x] **T3.5 [M] Session-tree branch summarization** — DONE (`7a612a9`).
  `summarize_branch(leaf_id, summarizer)` — provider-agnostic, empty-branch safe.
- [x] **T3.6 [S] Error / overflow taxonomies** — DONE (`f299ca1`). `FailureCategory`
  (8 members) + `classify_failure`; `MatrixCell.category` on both return paths.
- [x] **T3.7 [M] Provider + OAuth as an extension point** — DONE/verified-existing
  (`10afbe0`). Scan found it already built (`register_provider` + `AuthManager.register(AuthProvider)`
  + `create_provider` consults `get_token`→registered `login()`); added the composition
  test proving out-of-tree provider+auth resolve together. No new machinery needed.
- [x] ~~Shipped from this backlog already:~~ submit tool · faux provider · prompt-caching · model catalog · compat-flags · next-turn queue.

### Track 4 — Live-infra tier (needs docker/keys, not just code)
- [ ] **T4.1 [L] Docker repo-envs** — per-task containers so the SWE column is meaningful (unblocks T2.3).
- [ ] **T4.2 [M] Official-grader integration** — leaderboard-comparable numbers.
- [~] **T4.3 [S→M] lint-loop write path** — PARTIAL. Fixed a real derailment BUG
  (`LintFeedbackLoop` keyed lint-error detection on output-emptiness, so ruff's
  successful "All checks passed! / No Python files found" was fed back as a bogus
  fix task → model wrote lint commentary, not code; now keyed on exit code —
  verified: correct fenced output now produced) + added the missing `write_file`
  tool to the style. BUT answer-graded score still 0% at n=3 live; residual is a
  grading/harvest interaction on the real tasks, not the derailment or the tool.
  Reclassified [M]: next = inspect the matrix grade path for prose/tool_calls=0
  output vs `react` (which passes the same task). (`9c19e7a`)
- [x] **T4.4 [S] Preset `max_turns` budget parity** — DONE (`78e9334`).
  `CodingAgentAdapter.set_max_turns()` + InProcessRunner aligns it to
  `budget.max_llm_calls` on the partial path; honesty note updated.
- [ ] **T4.5 [M] Per-cell ATIF emission** — trajectories per matrix cell (the grid ran without them).
- [x] **T4.6 [S] `bench-compare` +4 loops** — DONE (`2f55023`). Roster 4→8
  (retry/plan-act/lint-feedback/autonomous); signature-tolerant factory; all 8
  build+run on faux.
- [x] **T4.7 [S] Codename `bench` subcommands** — DONE (`7eefa90`). Shared
  `dispatch_codename_bench()` → canonical bench-matrix; ferret/stoat/badger wired;
  live `ferret bench list` verified.

### Track 5 — Release discipline
- [ ] **T5.1 [S] Batch → next 0.9.x patch** when the user calls it (policy: patch-bumps only, batch and settle, no rushed ships). Gate + scrub + tag + publish + uvx-verify.

---

## DESIGN-FIRST TEAM WAVE (2026-07-06) — scan-informed specs

A codebase scan reshaped these from "build X" to "extend X" — four seams were
already partially present. Team of 3 builders on disjoint owned files; lead
(this session) owns shared-file wiring (`loop.py`, `matrix.py`, `main.py`),
the small Track-4 code items, and all gates. Constraints for every teammate:
real tests (no mocks-only), no brand/competitor names in code or docs, gate
your own files (`ruff` + targeted `pytest`) before reporting, and hand the lead
any shared-file change as a unified diff — do **not** edit shared files.

### Builder A — `error-taxonomy` (T3.6) [S]
- **Exists:** `matrix.py` cells carry a bare status string
  (`ok|budget_exhausted|error|timeout`) + a free-text error message.
- **Build (NEW file `chimera/eval/error_taxonomy.py`):** `FailureCategory` enum
  (budget_exhausted, tool_error, parse_error, empty_output, provider_error,
  timeout, grader_error, unknown) + `classify_failure(status, error_msg) ->
  FailureCategory` (substring/rule based, deterministic, documented). Tests:
  `tests/eval/test_error_taxonomy.py`.
- **Lead-applies:** the ~3-line `matrix.py` wiring to attach `.category` to a
  cell — hand it to the lead as a diff.
- **Accept:** classifier covers every status value + the common error strings;
  tests green; ruff clean.

### Builder B — `branch-summary` (T3.5) [M]
- **Exists:** `chimera/sessions/tree.py` already has an entry `summary` field,
  `add_compaction(summary, first_kept_id)`, `get_branch`, `get_messages`.
- **Build (extend `tree.py`):** `summarize_branch(leaf_id, summarizer)` — walk
  the branch via `get_messages`, call the injected `summarizer(messages)->str`
  callable (provider-agnostic; no hard provider dep), store the result through
  the existing compaction/summary path, return the summary id. Tests:
  `tests/sessions/test_branch_summary.py` with a fake summarizer.
- **Accept:** stale branch compacts to a stored summary; existing tree tests
  still pass; owns only `tree.py` + the new test.

### Builder C — `hook-points` (T3.1) [M]
- **Exists:** a real `chimera/hooks/` module (events/emitter/executor/loader +
  concrete hooks) firing at SESSION_START etc. via `_fire_loop_hook`.
- **Build:** audit `hooks/hook_types.py` + `events.py` for MISSING lifecycle
  points — specifically **pre/post tool-call** and **pre/post turn** — add the
  missing event types + a clean public `on(event, callback)` registration
  helper on the emitter. Tests: `tests/hooks/test_hook_points.py` proving a
  registered callback fires for each new point (drive the emitter directly).
- **Lead-applies:** the `loop.py`/`tool_executor.py` fire-site patch — hand it
  to the lead as a diff (those are shared files).
- **Accept:** new points registerable + fire in a unit test; no shared-file
  edits by the builder; ruff clean.

### Lead items (this session, parallel to builders)
- T4.3 lint-loop write path · T4.4 preset `max_turns` ← `budget.max_llm_calls`
  parity · T4.6 `bench-compare` +4 loops · T4.7 codename `bench` stubs ·
  apply the three builder diffs · full-suite gate · merge.
- Deferred from this wave (fuzzier, needs its own design): T3.2 UI registration
  [L], T3.3 hot-reload, T3.4 orchestrator daemon [L], T3.7 provider+OAuth
  extension point.


---

## RECONCILIATION 2026-07-09 (post integrity-war + staging wave)

- [x] **Grading integrity** — errored/empty runs can't pass (`0275ec3`); HumanEval+
  checker now invoked; pre-fix HumanEval+ numbers formally invalidated in the
  scorecard doc (`2709df1`).
- [x] **Cell-status aggregation** — `partial_error` for mixed cells (`a44a687`).
- [x] **Concurrency discipline** — grid capped at 4 (`c2e78f4`); playbook 13 +
  `scripts/verify_status.py` (8-check harness, `c1e4992`) enforce it.
- [x] **T4.2-equivalent for SWE: faithful grading** — FAIL_TO_PASS/PASS_TO_PASS,
  exit-code authoritative, conda auto-activation, vacuity guard (`ad8842d`+`9e33ef1`).
- [x] **swe-modal live infra proof** — per-instance image boots on Modal, patch
  applies (`data/swe-modal-smoke.json`).
- [x] **Staging wave** — runnable 7→11 benches / 3,678 tasks (`d6e6dc6`);
  tau-bench = code-defined upstream, not stageable (honest skip).
- [ ] **Full-dataset post-fix scorecard** — flagship × 5 full columns RUNNING on
  Modal (1,644 execs); publish after the integrity scan.
- [ ] Still open: T3.4 orchestrator daemon [L] · Track 2 external-CLI fidelity
  (needs installs) · async BudgetedProvider cost gap · T4.5 ATIF emission ·
  remaining data-gated benches (webarena, swe-lancer, multi-swe, …) · T5 release.

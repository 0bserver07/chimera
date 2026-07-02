---
title: Chimera Specs — Index & Status
---

# Chimera Specs — Index & Status

Design specs live in `docs/specs/`. This file is the tracker: every spec, what
it covers, its implementation status, and where the code lives. It mirrors the
[benchmark tracker](../benchmarks/README.md).

**Status legend:** ✅ Shipped · 🟡 Partial (implemented in part, or phased and
in progress) · 🔵 Design (proposal, not yet implemented).

| Spec | Covers | Status | Code / notes |
|------|--------|--------|--------------|
| [interactive-frontends](interactive-frontends.md) | Single-agent TUI + multi-agent multiplexer, as additive frontends over the agent driver (REPL unchanged) | ✅ Shipped | All 3 phases — `chimera code --tui` (single) · `--tui --models a,b,c` + `chimera otter --multiplex` (multiplexer), `chimera/tui/`. See phase checklist below |
| [comparative-bench-cli](comparative-bench-cli.md) | `chimera bench-compare` — controlled comparative matrix under uniform budgets | ✅ Shipped | `chimera/core/budget.py`, `bench-compare` CLI |
| [harbor-task-adapter](harbor-task-adapter.md) | Harbor / DeepSWE task-format adapter | ✅ Shipped | `chimera/eval/benchmarks/harbor.py` |
| [atif-trajectory-emission](atif-trajectory-emission.md) | ATIF v1.7 trajectory emit / validate / read | ✅ Shipped | `chimera/atif/` |
| [field-guide](field-guide.md) | Field Guide to Coding Agents (site pages) | ✅ Shipped | `site/` field-guide section |
| [coding-agent-harness-integration](coding-agent-harness-integration.md) | Drive the assembled CodingAgent through the eval Harness | 🟡 Partial | `chimera/eval/coding_agent_adapter.py` (in progress) |
| [modal-amd64-programbench-grading](modal-amd64-programbench-grading.md) | Modal amd64 sandbox for ProgramBench cleanroom grading (unusable under QEMU on arm64 dev machines) | 🔵 Design | Proposal; prior art in `scratch_modal_grade.py` (#160, cloud-sandbox track #144) |
| [formal-verification-integration](formal-verification-integration.md) | Z3 / Lean verifiers + CEGIS loop | 🟡 Partial | `chimera/training/strategies/cegis.py`; verifiers design |
| [formal-dsl-grammar](formal-dsl-grammar.md) | GrammarConstraint, SyGuS bridge | 🔵 Design | not yet implemented |
| [programming-by-example](programming-by-example.md) | ExampleSpec from I/O pairs | 🔵 Design | not yet implemented |
| [neural-guided-search](neural-guided-search.md) | Learned search policy from synthesis traces | 🔵 Design | not yet implemented |

## Phased specs

Specs delivered in phases track their phases here.

### interactive-frontends — TUI & multiplexer

- [x] **Phase 1 — single-agent TUI** (`87acb8c`): regions, streaming/commit,
      tool-call rendering, input router (submit/steer/local), slash commands,
      keybindings, turn lifecycle + cancel, errors. `chimera code --tui`,
      verified live on GLM-5.2.
- [x] **Phase 2 — multiplexer**: N lanes racing one task in isolated
      workspaces (git worktree + copy fallback, R-ISO-1…5), responsive pane
      layout (tabs on narrow terminals), broadcast vs targeted input, per-lane
      telemetry + cohort summary with first-to-finish, concurrency cap, and
      cohort manifest + transcript/diff persistence + zip export. Code:
      `chimera/tui/{multiplex,lane,cohort,workspace,routing,render}.py`;
      launch via `chimera code --tui --models a,b,c` or
      `chimera otter --multiplex a,b,c`. Verified live on GLM-5.2 + GLM-4.6
      (concurrent, isolated, ranked by finish).
- [x] **Phase 3 — polish & depth** (detailed in spec §13) — **complete**:
  - [x] 13.1 in-UI cohort comparison view (scoreboard + per-lane diff viewer) — shipped (PR #162)
  - [x] 13.2 resumable per-lane sessions (`--resume <cohort-id>`, `--list-cohorts`) — shipped
  - [x] 13.3 heterogeneous lanes — per-lane preset + posture + **real loop swap** (`model:preset:loop`; loops `plan-execute`/`reflexion`/`tot` via the loop adapter) — shipped
  - [x] 13.4 reasoning display (`thinking_chunk` event path + collapsed-by-default rendering, Ctrl+E) — shipped, live-verified on GLM-5.2 thinking
  - [x] 13.5 multi-line input (`PromptArea`: Enter submits, Ctrl+J newline, history recall) · 13.6 slash autocomplete (hint line + Tab) — shipped
  - [x] 13.7 per-lane sidebar (tool-call timeline, Ctrl+T, narrow auto-hide) · 13.8 richer diffs (per-file nav n/p + split/unified s) — shipped

The load-bearing Phase-2 open decision — **workspace isolation** (§6.2) — was
resolved to *git worktree per lane, with a directory-copy fallback for non-git
sources* (`--isolation auto|worktree|copy|inplace`; `inplace` opt-in only, and
unsafe for file-writing cohorts). Remaining §11 decisions (default routing =
broadcast, reasoning hidden, cohort budget off) took the spec's stated defaults;
outcome-diff UI is deferred to Phase 3.

## Convention

- One spec per file in `docs/specs/`, named by topic.
- Each spec SHOULD carry a `**Status:**` header line near the top.
- When you add a spec, add a row above. When you ship (or partially ship) one,
  update its status and code pointer, and tick its phase checklist.

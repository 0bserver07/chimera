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
| [interactive-frontends](interactive-frontends.md) | Single-agent TUI + multi-agent multiplexer, as additive frontends over the agent driver (REPL unchanged) | 🟡 Partial | Phase 1 shipped — `chimera code --tui`, `chimera/tui/`. Phase 2 (multiplexer) is design. See phase checklist below |
| [comparative-bench-cli](comparative-bench-cli.md) | `chimera bench-compare` — controlled comparative matrix under uniform budgets | ✅ Shipped | `chimera/core/budget.py`, `bench-compare` CLI |
| [harbor-task-adapter](harbor-task-adapter.md) | Harbor / DeepSWE task-format adapter | ✅ Shipped | `chimera/eval/benchmarks/harbor.py` |
| [atif-trajectory-emission](atif-trajectory-emission.md) | ATIF v1.7 trajectory emit / validate / read | ✅ Shipped | `chimera/atif/` |
| [field-guide](field-guide.md) | Field Guide to Coding Agents (site pages) | ✅ Shipped | `site/` field-guide section |
| [coding-agent-harness-integration](coding-agent-harness-integration.md) | Drive the assembled CodingAgent through the eval Harness | 🟡 Partial | `chimera/eval/coding_agent_adapter.py` (in progress) |
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
- [ ] **Phase 2 — multiplexer**: N lanes, per-lane isolation (R-ISO-1…5),
      responsive pane layout, broadcast vs targeted input, comparison
      telemetry + cohort summary, concurrency caps, persistence/export.
- [ ] **Phase 3 — polish**: outcome diff/export, sidebar, slash autocomplete,
      reasoning collapse, richer diff forms, multi-line input.

Open decisions for Phase 2 are enumerated in the spec (§11); the load-bearing
one is **workspace isolation** (§6.2) — N file-writing agents must not share a
mutable tree.

## Convention

- One spec per file in `docs/specs/`, named by topic.
- Each spec SHOULD carry a `**Status:**` header line near the top.
- When you add a spec, add a row above. When you ship (or partially ship) one,
  update its status and code pointer, and tick its phase checklist.

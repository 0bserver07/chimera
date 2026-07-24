---
title: Persistence & config model
description: What Chimera persists, where, when — the three stores (sessions, event log, cohorts) and the one unified config chain that feeds the TUI and skills.
---

# Persistence & config model

Chimera has **three persistence mechanisms** and, as of #173, **one config
chain**. This note maps them so the "which thing writes what, where, when"
question has a single answer, and records what was deliberately *not* merged.

## The three persistence stores

| Store | Module | Where | Persists what | When |
|---|---|---|---|---|
| **Session tree** | `chimera/sessions/tree.py` | `~/.chimera/sessions/<id>.jsonl` | The conversation as an append-only JSONL tree (typed entries: header, message, compaction boundary, label, state-change, generic) with in-place branching | Per turn / on save; append-only (reversible — you can branch back before a compaction) |
| **Event log** | `chimera/sessions/eventlog/` | `~/.chimera/sessions/<id>/events/` | Event-sourced record with file locking, crash recovery, gap detection — the durable spine a session can be reconstructed from | Per event, atomic append under a lock |
| **Cohorts** | `chimera/tui/cohort.py` | `~/.chimera/cohorts/<cohort-id>/` | A multiplexer race artifact: `manifest.json`, ranked summary, per-lane transcript + diff; the unit `--resume` / `/cohorts` reloads | On multiplexer exit (persist-before-teardown); pruned per policy on next launch |

**Why three, not one.** They serve different lifetimes and consumers: the
session tree is the live conversation model (branch/rewind/compact); the event
log is the crash-durable spine; the cohort artifact is a *comparison result*
(N lanes, diffs, scoreboard) that outlives its worktrees. A cohort references
the same kind of history the session tree holds, but adds cross-lane structure
(manifest, ranking, per-lane diffs) that is not a session concept.

## Cohort retention (auto-pruning) — #173

Bare `chimera code --tui` writes one cohort per session; without a cap they
accumulate. Configure a policy under `[tui.cohorts]`:

```toml
[tui.cohorts]
retain = 20            # keep only the newest 20 cohorts
max-age-days = 30      # and/or drop cohorts older than 30 days
```

- **OFF by default** — no config means nothing is ever pruned (the
  data-preserving default; nobody loses work they did not ask to discard).
- Pruning runs at multiplexer exit, **after** the current cohort is persisted,
  and the cohort being run or resumed is **never** deleted (passed to
  `prune_cohorts(exclude=…)`).
- Only directories carrying a `manifest.json` are considered — unrelated files
  under the cohort root are never touched. Deletion is best-effort: a locked or
  vanished directory is skipped, never fatal.
- `retain` is a hard floor (the newest N always survive); `max-age-days` drops
  older cohorts by their manifest `created_at` (directory mtime as fallback).

## The one config chain

Historically the TUI read **two dialects**: keybindings from a TOML
`config.toml` and the status line / skills toggles from `config.{yaml,yml,json}`
across a different scope set. `chimera/config/user_config.py` unifies them.

**Canonical format: TOML** at `~/.chimera/config.toml` (the file the codename
CLIs already read, and the only format the standard library parses alone —
zero-dependency-core). YAML/JSON in the same scopes remain a read-time
**compatibility shim** so files written against the older status-line loader
keep loading unchanged.

**Precedence, lowest first** (higher overrides lower, key-by-key deep merge):

1. `~/.config/chimera/` (XDG)
2. `~/.chimera/` (or `$CHIMERA_CONFIG_HOME`)
3. `<project>/.chimera/` (project scope, status-line chain only)

Within one scope every present `config.*` deep-merges, with `config.toml`
winning a key collision. A missing or broken file degrades to an empty
contribution — a stale config never blocks startup.

Consumers, all reading through this one loader:

| Consumer | Reads | Function |
|---|---|---|
| Keybindings (`tui.keybinds`) | user scope | `keys.load_user_keybinds` → `load_user_scope_config` |
| Skills toggles (`skills.*`) | user scope | `discovery.resolve_foreign_config` → `load_user_scope_config` |
| Status line / title (`tui.status_line`, `tui.title`) | XDG/user/project | `statusline.load_tui_config` → `load_tui_config` |
| Cohort retention (`tui.cohorts`) | XDG/user/project | `cohort.load_cohort_retention` → `load_tui_config` |

## Deliberately NOT merged (deferred convergence)

A full three-way persistence unification was out of scope for this change and
would rewrite live on-disk formats. Left as-is, with rationale:

- **Session tree ↔ event log stay separate stores.** They already cooperate
  (a session can be rebuilt from the log); collapsing them into one format is a
  storage-layer rewrite with migration risk, not a config change.
- **Cohort persistence keeps its own JSON manifest** rather than adopting the
  event log's locked-append discipline. Cohorts are written once at teardown
  (not per-event), so the lock/gap-detection machinery buys little; adopting it
  would be churn without a correctness gain. Revisit if concurrent
  multiplexer instances ever write the same cohort root.

These are convergence *opportunities*, not defects — the map above is the
single source of truth until one is taken.

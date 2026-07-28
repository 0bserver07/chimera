---
title: "Seeing and reclaiming your storage"
description: "chimera doctor's storage section reports every declared store plus any directory nothing claims; chimera gc turns opt-in retention into a dry run you can read before anything is touched."
---

Two commands sit on top of the path registry (see
[Where Chimera keeps your data](/chimera/guides/storage-and-paths/)):

- **`chimera doctor --section storage`** — what is on disk, and what nothing
  claims.
- **`chimera gc`** — what retention *would* reclaim. Dry run by default.

They exist because of a specific failure. A 2.0 GB checkpoint tree — a full
copy of a repo including `.venv` and `node_modules` — sat on disk for four
months. Nothing was wrong with any single component; there was simply no
surface that could say "this directory is here and nobody declared it."

## Seeing what is there

```bash
chimera doctor --section storage
```

```
  [storage]
  CHECK                      STATUS  DETAIL
  -------------------------  ------  ------
  storage.root               ok      /Users/you/.chimera · 538.5 MB · 2002 files
  storage.datasets           ok      /Users/you/.chimera/datasets · 150.7 MB · 14 entries · newest 19.0d / oldest 90.6d · never prunable
  storage.cohorts            ok      /Users/you/.chimera/cohorts · 40.7 MB · 16 entries · newest 2.0d / oldest 26.5d · keep forever
  storage.sessions           ok      /Users/you/.chimera/sessions · 2.0 kB · 2 entries · newest 128.5d / oldest 130.2d · keep forever
  storage.shares             ok      /Users/you/.chimera/shares · absent
  ...
  storage.orphans            ok      none — every directory on disk is declared
```

One row per registry store, both scopes, **including absent ones**. That is
deliberate: "declared and empty" and "not declared at all" are different facts,
and a report that omitted the first could not be trusted about the second.

`--section storage` skips every network and subprocess probe, which makes it
cheap enough to run in a script or a shell prompt. Plain `chimera doctor` runs
it alongside the provider checks. Sizes are decimal (1000-based) MB/GB.

### Orphans

Any directory the registry does not name is reported:

```
  storage.orphan.chimera_checkpoints  warn  /repo/.chimera_checkpoints · 2.0 GB · 41823 files ·
                                            .chimera_checkpoints sits beside .chimera, not inside
                                            it — no registry row names this path
                                            hint: archive or relocate it, or declare it in
                                            chimera/config/paths.py — `chimera gc` cannot touch
                                            what the registry does not name
```

Three locations are scanned:

1. the storage root (`~/.chimera` or wherever you relocated it),
2. the project state dir (`<project>/.chimera`),
3. **project-root `.chimera*` siblings** — `<project>/.chimera_checkpoints` and
   anything shaped like it.

That third one is the one that matters. It sits *beside* `<project>/.chimera`,
not inside it, so a scan of the two roots alone walks straight past it — which
is exactly how the 2 GB tree stayed invisible. If you are extending the scan,
this is the case to keep a test on.

Loose *files* at a scope root are not reported. `config.toml`, `settings.json`,
`todo.json` and `rules.md` legitimately live there, and flagging them would
train you to skim past the section.

An orphan is a `warn`, never a `fail` — it is something to look at, not a
broken install, so it will not fail a CI health check.

### JSON

```bash
chimera doctor --section storage --json | jq '.checks[] | select(.name=="storage.cohorts") | .data'
```

Storage rows carry a `data` object with real fields — `size_bytes`,
`entries`, `newest_age_days`, `retention` — so nothing has to parse the
rendered cell. Rows from the older provider probes are unchanged.

## Reclaiming: `chimera gc`

Retention is opt-in. Declare it per store (details in the
[registry guide](/chimera/guides/storage-and-paths/#retention)):

```toml
# ~/.chimera/config.toml
[storage.sessions]
retain = 200
max-age-days = 90
```

Then look before you act:

```bash
chimera gc
```

```
chimera gc (dry run — nothing changed; pass --apply to act):

  sessions  /Users/you/.chimera/sessions
    2026-03-02-a1b2.jsonl      12.4 kB  retain=200 (position 201)
    2026-02-27-c3d4.jsonl       9.1 kB  retain=200 (position 202)
    -> 2 entries, 21.5 kB

  no retention configured: cohorts, eventlog, projects, tasks, experiment-runs, ...
  never prunable (structural): datasets, history, function_synthesis, cron, ...
  project-state: skipped — root contains the 'project-sessions' store

  total: 2 candidate(s), 21.5 kB across 1 store(s)
  run again with --apply to act, or --archive DIR to relocate.
```

Every candidate names the rule that selected it. The skip lines account for the
rest of the registry — silence about a store is what let the original problem
grow, so nothing is quietly omitted.

| Flag | Effect |
|---|---|
| *(none)* | Dry run. Prints the plan, changes nothing. |
| `--apply` | Act on the plan. |
| `--archive DIR` | With `--apply`, **move** candidates into `DIR/<store>/` instead of deleting. |
| `--store NAME` | Limit to one store (repeatable). An undeclared name exits 2. |
| `--project PATH` | Project root for project-scope stores (default: cwd). |
| `--json` | Machine-readable plan. |

Prefer `--archive`:

```bash
chimera gc --apply --archive ~/chimera-attic
```

### What `gc` structurally cannot do

- **Touch a store with no retention configured.** `chimera gc --apply` on a
  machine that has never written a `[storage.*]` table is a no-op.
- **Touch a `never prunable` store.** `datasets`, `function_synthesis`,
  `tokens`, `cron` and the rest ignore any retention you write for them.
- **Touch a path the registry does not name.** Candidates are built by
  iterating the registry, and every one is revalidated before the first
  deletion: the store must be declared and prunable, and the path must be a
  direct child of the declared root. Validation runs over the whole batch
  first, so a bad candidate aborts the run intact rather than half-applied.
  There is no code path from an undeclared directory to a deletion — which
  includes everything under `data/`.
- **Prune a store that contains another store.** `project-state` *is*
  `<project>/.chimera`, whose children are other stores plus your live config.
  One retention line there would have deleted `todo.json`. Parent stores are
  skipped, with the reason printed.

Orphans are reported by `doctor` and **never** reclaimed by `gc` — `gc` only
knows the registry's vocabulary. Move them yourself, or add a `Store` row if
they turn out to be something Chimera should own.

## Cohorts ride the same engine

`[tui.cohorts]` / `[storage.cohorts]` retention predates all of this. It now
calls the same selector rather than keeping a second copy of the rules, so
`chimera gc` and the TUI's auto-prune cannot drift apart. Its own guarantee is
intact: the cohort you are running or resuming is never pruned, and it still
only considers directories carrying a `manifest.json`.

## From Python

```python
from chimera.config.storage import find_orphans, plan_gc, report_stores, apply_prune

for report in report_stores():
    print(report.store.label, report.size_bytes, report.retention_label)

for orphan in find_orphans():          # largest first
    print(orphan.path, orphan.size_bytes, orphan.reason)

plan = plan_gc()                       # never touches the filesystem
for candidate in plan.candidates:
    print(candidate.store, candidate.entry.id, candidate.rule)

apply_prune(plan.candidates)           # the only destructive call
apply_prune(plan.candidates, archive_to=Path("~/attic").expanduser())
```

`apply_prune` raises `UnknownStore` or `ValueError` — before deleting anything
— if a candidate names a store the registry does not declare, names a
never-prunable store, or points outside its declared root.

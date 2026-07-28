---
title: "Where Chimera keeps your data"
description: "One declared registry for every on-disk store: what lives where, how to relocate it with $CHIMERA_HOME or [storage] root, which stores can never be reclaimed, and how to declare retention before chimera gc lands."
---

Chimera writes to two places and only two: a **user-scope root** (`~/.chimera`
by default) and a **project state directory** (`<project>/.chimera`). Every
store inside them is declared in one registry, `chimera/config/paths.py`. If a
directory is not in that registry, Chimera did not put it there.

That registry is what makes the rest of this page possible: a single place to
relocate everything, a single place to declare retention, and — once `chimera
gc` lands — a structural guarantee that no lifecycle tool can touch a path the
registry never named.

**If you have never configured anything, nothing has moved.** With no
environment variable and no config file, every path resolves exactly where it
always did. You can stop reading here.

## Relocating the root

Two ways, highest precedence first:

```bash
export CHIMERA_HOME=/data/chimera        # 1. environment
```

```toml
# ~/.chimera/config.toml                 # 2. config
[storage]
root = "/data/chimera"
```

Otherwise the root is `~/.chimera`. `~` is expanded in both forms.

`[storage]` is read from the same chain as every other Chimera setting — XDG
(`~/.config/chimera/`) < user (`~/.chimera/`) < project (`<project>/.chimera/`)
— so a project can pin its own root, and any of `config.toml`, `config.yaml`,
`config.yml`, `config.json` will do (TOML is canonical).

**One deliberate exception:** `config.toml` itself is always discovered at
`~/.chimera/`, never under a relocated root. A file cannot live inside the
directory it relocates — reading it back would be circular. So with
`root = "/data/chimera"` set, your config stays at `~/.chimera/config.toml`
while your sessions, cohorts, and datasets move to `/data/chimera/`.

Project state (`<project>/.chimera`) is never relocated. It belongs to the
project by definition.

## The registry

| Store | Scope | Path under its root | Written by | Prunable |
|---|---|---|---|---|
| `datasets` | user | `datasets` | `chimera/eval/datasets.py` | **never** |
| `cohorts` | user | `cohorts` | `chimera/tui/cohort.py` | yes |
| `sessions` | user | `sessions` | `chimera/cli/code.py` | yes |
| `eventlog` | user | `eventlog` | `chimera/sessions/eventlog/` | yes |
| `history` | user | `history` | `chimera/cli/code.py` | **never** |
| `projects` | user | `projects` | `chimera/tools/todo.py` | yes |
| `function_synthesis` | user | `function_synthesis` | `chimera/function_synthesis/` | **never** |
| `tasks` | user | `tasks` | `chimera/tools/task_tool.py` | yes |
| `experiment-runs` | user | `experiment-runs` | `scripts/experiments/` | yes |
| `exports` | user | `exports` | `chimera/sessions/share.py` | yes |
| `shares` | user | `shares` | `chimera/otter/share_cmd.py` | yes |
| `snapshots` | user | `snapshots` | `chimera/otter/snapshot.py` | yes |
| `worktrees` | user | `worktrees` | `chimera/otter/worktree.py` | yes |
| `teams` | user | `teams` | `chimera/cli/agent_teams.py` | yes |
| `plans` | user | `plans` | `chimera/stoat/plan_mode.py` | yes |
| `cache` | user | `cache` | `chimera/skills/discovery.py` | yes |
| `cron` | user | `cron` | `chimera/tools/cron_tools.py` | **never** |
| `learning` | user | `learning` | `chimera/learning/store.py` | **never** |
| `tokens` | user | `tokens` | `chimera/mcp/oauth.py` | **never** |
| `run` | user | `run` | `chimera/otter/server_pidfile.py` | **never** |
| `agents` | user | `agents` | `chimera/agents/team_roles.py` | **never** |
| `skills` | user | `skills` | `chimera/skills/discovery.py` | **never** |
| `completion` | user | `completion` | `chimera/cli/completion.py` | **never** |
| `profiles` | user | `profiles` | `chimera/ferret/cli.py` | **never** |
| `badger` | user | `badger` | `chimera/badger/slash.py` | **never** |
| `ferret` | user | `ferret` | `chimera/ferret/subcommands/mcp_manage.py` | **never** |
| `shrew` | user | `shrew` | `chimera/shrew/model_profiles.py` | **never** |
| `stoat` | user | `stoat` | `chimera/stoat/hooks.py` | **never** |
| `project-state` | project | *(the dir itself)* | `chimera/commands/builtins.py` | yes |
| `project-sessions` | project | `sessions` | `chimera/assembly/coding_agent.py` | yes |
| `project-agents` | project | `agents` | `chimera/agents/team_roles.py` | **never** |
| `project-checkpoints` | project | `checkpoints` | `chimera/checkpoints.py` | yes |
| `project-snapshots` | project | `snapshots` | `chimera/commands/builtins.py` | yes |
| `project-memory` | project | `memory` | `chimera/core/memory.py` | **never** |
| `project-prompts` | project | `prompts` | `chimera/core/prompt_template.py` | **never** |
| `project-skills` | project | `skills` | `chimera/skills/discovery.py` | **never** |

Single files also live at the root and are not stores: `config.toml`,
`mcp.json`, `settings.json`, `permissions.json`, `credentials.json`,
`sessions.db`, `persistent_memory.json`, `loop_detector_state.json`.

`chimera doctor` will grow a storage section that prints this table with sizes
and ages, plus any directory it finds that the registry does *not* claim.

### Why "never prunable" is not a default

Six categories are structurally exempt, meaning no config file can opt them in:

- **`datasets`** and **`function_synthesis`** — deliberately staged benchmark
  inputs and synthesised model artifacts. Expensive to rebuild, sometimes
  impossible for a pinned revision.
- **`agents`, `skills`, `profiles`, `project-memory`, `project-prompts`,
  `project-skills`** — things *you* authored. Input, not output.
- **`tokens`** — credentials.
- **`cron`** — pruning a job file silently unschedules work.
- **`run`** — live pidfiles.
- **`history`** — a single readline file, not a directory; readline caps it
  itself.

Write `[storage.datasets] retain = 1` and it is read, ignored, and the store
keeps everything.

## Retention

Retention is **opt-in and off by default**. Declaring it changes nothing on its
own — `chimera gc` (shipping next) is the only thing that will act on it, and
its dry run is the default.

```toml
[storage.sessions]
retain = 200            # keep the newest 200   (absent = keep forever)
max-age-days = 90       # and/or drop anything older

[storage.eventlog]
retain = 50
```

Both keys accept the underscore spelling (`max_age_days`). A missing,
zero, negative, or unparseable value disables that knob rather than guessing.
Store names containing a dash also answer to underscores, so
`[storage.experiment_runs]` and `[storage.experiment-runs]` are the same table.

### Cohort retention (`[tui.cohorts]`)

Cohort retention shipped before `[storage]` existed. Both spellings work:

```toml
[storage.cohorts]       # preferred
retain = 20
max-age-days = 30
```

```toml
[tui.cohorts]           # legacy alias — still read
retain = 20
max-age-days = 30
```

`[storage.cohorts]` wins if both are present. Nothing you already wrote needs
to change.

## Environment overrides that still work

These predate the registry and are honored exactly as before:

| Variable | Effect |
|---|---|
| `CHIMERA_HOME` | The storage root. Beats `[storage] root`. |
| `CHIMERA_DATASETS_DIR` | Relocates the `datasets` store alone. Beats the root. |
| `CHIMERA_FS_HOME` | Relocates the `function_synthesis` store alone. |
| `CHIMERA_PB_RUNS` | Relocates the `pb-runs` *subtree* of `experiment-runs`, not the store. |
| `CHIMERA_TEAMS_HOME` | Relocates the `teams` store for one run. |
| `CHIMERA_CRON_DIR` | Relocates the `cron` store for one run. |
| `CHIMERA_CONFIG_HOME` | Where `config.toml` is read from (config only, not storage). |

Per-benchmark dataset overrides (`CHIMERA_TAU_BENCH_PATH` and friends) are
unchanged; when unset, those benchmarks now resolve under the `datasets` store,
so `CHIMERA_DATASETS_DIR` relocates them consistently with `chimera bench-fetch`.

## From Python

```python
from chimera.config.paths import (
    all_stores, chimera_home, project_state_dir, store_path, store_retention,
)

chimera_home()                          # PosixPath('/Users/you/.chimera')
store_path("sessions")                  # .../.chimera/sessions
store_path("project-skills", "/repo")   # /repo/.chimera/skills
project_state_dir("/repo")              # /repo/.chimera

store_retention("sessions").retain      # 200, or None when unconfigured

for store in all_stores():
    print(store.label, store.writer, store.prunable, store.note)
```

Nothing here creates a directory — callers `mkdir` when they are about to
write. Resolution happens on every call, never at import, so setting
`CHIMERA_HOME` in a test or an embedding host is honored by code that was
imported earlier. An unknown store name raises `UnknownStore` rather than
resolving to a plausible-looking path.

### Adding a store

Add a row. It is data, not a code path:

```python
Store(
    name="my-store",
    scope="user",
    rel="my-store",
    writer="chimera/mypackage/writer.py",
    prunable=True,
    note="What a reader needs that the columns cannot say.",
)
```

Then use `store_path("my-store")` at the write site. Do not compose
`Path.home() / ".chimera" / ...` — a directory the registry does not name is
reported as an orphan, and the one-definition property is the whole point.

## Two things this does not do

- **It does not delete anything.** No code path in the registry removes files.
- **It does not prune on its own.** Retention is read here and acted on only by
  `chimera gc`, explicitly, dry-run first.

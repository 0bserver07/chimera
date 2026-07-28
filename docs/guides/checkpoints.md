---
title: "Checkpoints: what they capture and where they live"
description: "Checkpoints snapshot source, not dependencies: what is excluded and why, where the files land, how restore treats what it never captured, reading checkpoints written before the move, and how to turn on retain-N."
---

A checkpoint is a snapshot of your **workspace source**. `env.checkpoint()`
returns an ID, `env.restore(id)` puts the files back. Undo in the REPL, the
transaction wrapper, and the tree-search strategies all sit on top of it.

The important word is *source*. A checkpoint deliberately does not contain your
virtualenv, your `node_modules`, your build output, or your `.git` directory.

## Why it excludes those

It used not to. `LocalEnvironment.checkpoint()` copied the entire working
directory, so on a real project a single checkpoint reached **2.0 GB** —
containing a `.venv`, 759 MB of `site/node_modules`, and a duplicate of an
unrelated output directory. Nothing surfaced it; the directory was also created
on *every* `setup()`, so deleting it just brought it back.

Measured on this repository, the same checkpoint is now **1,778.4 MB → 170.3 MB**
— 90.4% smaller, and 6.5 seconds instead of 40.

The excluded set is `NOT_SOURCE_DIRS` in `chimera/config/ignore.py`: version
control internals, virtualenvs and installed packages, `node_modules`, tool
caches, editor state, and build output. It is the same list `list_files`,
`repo_map`, and definition lookup use to decide what is not worth showing you,
so a directory a tool hides from the model is a directory a checkpoint does not
copy.

Exclusion applies at **every depth**. A nested `site/node_modules` is skipped
exactly like a top-level one — that nesting was the single largest component of
the 2.0 GB tree.

## Restore does not delete what it did not capture

This is the half that matters in practice. `restore()` clears the workspace
before copying files back, but it skips the same directories the checkpoint
skipped:

```python
env.write_file("app.py", "v1")
cp = env.checkpoint()          # .venv is not copied
env.write_file("app.py", "v2")
env.restore(cp)                # app.py is back at v1; .venv is untouched
```

Your virtualenv, your `node_modules`, your `.git` history, and your project's
`.chimera` state survive a restore. A checkpoint that excluded them and a
restore that deleted them would be strictly worse than the bug being fixed.

## Where the files live

```
<workdir>/.chimera/checkpoints/<id>/
```

That is the `project-checkpoints` store in the path registry
([Where Chimera keeps your data](storage-and-paths.md)), so `chimera doctor`
can account for it and lifecycle tooling can reason about it. The directory is
created the first time something checkpoints — not on every `setup()`.

### Checkpoints taken before this change

They lived in `<workdir>/.chimera_checkpoints`, a *sibling* of `.chimera`
rather than a child. Those trees are:

- **still restorable** — `restore()` looks in the registry store first, then
  the legacy directory, so an ID from an old session still resolves;
- **never written to again** — new checkpoints only go to the registry store;
- **never deleted or pruned** — retention does not reach them, by construction.

New IDs are allocated above the highest ID in *both* locations, so one ID can
never mean two different snapshots.

If you want the disk space back, move the directory yourself:

```bash
mv .chimera_checkpoints ~/archive/checkpoints-$(date +%F)
```

Nothing in Chimera will do that for you.

## Keeping only the last N

Retention is **off by default**: with no configuration, every checkpoint is
kept forever. To bound it, add one line to any config file in the chain
(`~/.chimera/config.toml`, or `<project>/.chimera/config.toml`):

```toml
[storage.checkpoints]
retain = 5
```

Now `checkpoint()` drops the oldest beyond the newest five, immediately after
writing. The checkpoint just written is never a candidate, and the legacy
directory is never touched. Remove the line and nothing is ever deleted again.

## The size warning

Past 256 MB a checkpoint emits a `LargeCheckpointWarning` naming the path and
the size. It is not a limit — the checkpoint is written either way — it is the
signal that was missing for four months. Because vendored and build directories
are already excluded, a warning now means real workspace content, which is
usually worth looking at.

To raise or lower it in code:

```python
import chimera.env.local as local

local.CHECKPOINT_SIZE_WARN_BYTES = 1024 * 1024 * 1024   # 1 GB
```

To silence it without changing the threshold:

```python
import warnings
from chimera.env.local import LargeCheckpointWarning

warnings.filterwarnings("ignore", category=LargeCheckpointWarning)
```

It has its own category so silencing it silences nothing else.

## Git-based checkpointing instead

`GitEnvironment` checkpoints with real commits rather than file copies, which
sidesteps the size question entirely — `.gitignore` already says what is not
source:

```python
from chimera.env.git_env import GitEnvironment

env = GitEnvironment(workdir="/path/to/project")
env.setup()
sha = env.checkpoint()      # a commit
env.restore(sha)            # git checkout
```

Use it when the workspace is a git repository and you want checkpoints you can
inspect with `git log`.

## What is not covered

- **A checkpoint is not a backup.** It lives inside the workspace it snapshots;
  losing the directory loses both.
- **Excluded directories are not versioned at all.** If a run's result depends
  on the exact contents of `node_modules`, a checkpoint will not reproduce it —
  pin your lockfile instead.
- **Nothing prunes without configuration.** If checkpoints are accumulating and
  you did not set `retain`, they will keep accumulating.

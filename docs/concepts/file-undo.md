---
title: "File Undo (otter /undo and /redo)"
description: "How otter's git-shadow file snapshot store rewinds both the conversation and any files the agent touched."
---

Otter's `/undo` rewinds **the conversation and any files the agent touched in that turn** — not just the message history. It works in any directory, with or without git, by maintaining a content-addressed file snapshot store keyed per session.

## How `/undo` and `/redo` work

After every assistant turn, the otter REPL takes a snapshot:

1. **Checkpoint** — a `chimera.checkpoints.CheckpointManager` snapshots the workspace and pushes a `CheckpointInfo` onto the per-session undo stack.
2. **Conversation snapshot** — the session's `Context` messages are deep-copied so the conversation can be rolled back alongside the filesystem.
3. **File shadow** — the on-disk `FileSnapshotStore` records every file the agent's `FileTracker` reports as modified, content-addressed by SHA-256.
4. **Redo invalidation** — any pending redo entries are dropped (a fresh turn invalidates the redo path).

`/undo` pops the top of the undo stack, pushes it onto the redo stack, then restores the resulting top-of-undo (or the initial state if the stack is empty). `/redo` is the inverse: pop redo, restore, push back onto undo.

## On-disk layout

```
~/.chimera/snapshots/<session-id>/
    blobs/<sha256>                # content-addressed file payloads
    snaps/<snap-id>/manifest.json # {abs_path: sha256 | null}
```

Each per-turn manifest records every file modified at any point during the session, content-addressed via SHA-256 so unchanged files share a single blob. Files that did not exist at snap time are recorded as `null` so `restore` knows to delete them on rewind (matching `git checkout` semantics).

The store deliberately uses plain file copies rather than shelling out to `git`: otter sessions live in arbitrary working directories that may or may not be a git repo.

## Sentinel limit

Files larger than 25 MiB are not snapped (we record `null` instead) so a runaway log file doesn't balloon `~/.chimera`. Symlinks are followed.

## Prerequisites

- Chimera installed: `pip install chimera-run`
- An otter session (e.g. `otter chat`)
- Write access to `~/.chimera/snapshots/` (override with `$CHIMERA_SNAPSHOT_ROOT`)

## Slash commands

| Command | Effect |
|---|---|
| `/undo` | Roll the conversation and modified files back to the previous turn. |
| `/redo` | Re-apply the most recently undone turn. |
| `/new` | Discard the session, including its snapshot shadow. |

`/undo` is idempotent at the boundary: when the undo stack is empty, restoring the **initial** state is a no-op. The redo stack tracks however many `/undo`s have happened in a row.

## Python API

```python
from chimera.otter.snapshot import FileSnapshotStore

store = FileSnapshotStore(session_id="my-session")

# Snap the current state of files the agent modified
snap = store.snap(["/abs/path/a.py", "/abs/path/b.py"])

# Later, restore that snap
restored_paths = store.restore(snap.snap_id)

# Housekeeping
store.discard("snap-1-...")     # drop a single snap
store.gc_blobs()                # reclaim space; returns count removed
store.clear()                   # wipe the whole shadow for this session
```

## Override the snapshot root

Set `CHIMERA_SNAPSHOT_ROOT` to redirect the shadow under a tmp dir (used by tests, by sandboxed CI, or by isolated harnesses):

```bash
export CHIMERA_SNAPSHOT_ROOT=/tmp/chimera-snapshots
```

## Worked example

```text
otter> Read main.py and refactor the load() function.
... agent runs, edits main.py and tests/test_main.py ...

otter> /undo
[ok] rolled back 2 file(s); messages restored.

otter> /redo
[ok] re-applied; main.py and tests/test_main.py back to post-turn state.

otter> /new
[ok] session cleared; snapshot shadow wiped.
```

## Storage cost

Because the shadow is content-addressed, unchanged files across N turns cost **O(1)** storage (one shared blob), not O(N). `gc_blobs()` walks every manifest, builds the live-blob set, and unlinks orphans.

## See also

- [Hook events](./hook-events.md) — `FileChanged` and `CwdChanged` fire on tracked-file edits and working-directory changes.
- [Subagent profiles](./subagent-profiles.md) — `executor` + `reviewer` flow benefits from `/undo` after a regrettable change.

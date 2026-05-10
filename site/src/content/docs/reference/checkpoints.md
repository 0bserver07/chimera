---
title: "chimera.checkpoints"
description: "Reference for chimera.checkpoints — CheckpointManager (create, list, restore, undo)."
---

`chimera.checkpoints` lets a session save snapshots of conversation
state and the working tree, then roll back to a named or id-based
checkpoint.

## Top-level exports

```python
from chimera.checkpoints import CheckpointManager
```

| Method | Purpose |
|---|---|
| `create(name=None)` | Save a checkpoint. Returns a `(name, id)` tuple. |
| `list_checkpoints()` | Enumerate all checkpoints. |
| `restore_by_name(name)` | Restore a named checkpoint. |
| `restore_by_id(id)` | Restore by uuid. |
| `undo()` | Restore the most recent checkpoint. |

`CheckpointManager` is wired into the REPL via the `/checkpoint` slash
command and into the agent loop via `LoopConfig.checkpoint_manager=`.

## See also

- [`chimera.core`](/reference/core/) for the `LoopConfig` field.
- [`chimera.sessions`](/reference/sessions/) for the persistent session
  storage layer that checkpoints serialise into.

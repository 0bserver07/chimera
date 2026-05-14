---
title: "exit_worktree — remove, merge, or abandon a worktree"
description: "Cleanup the counterpart to enter_worktree. Choose remove (delete the working tree but keep the branch), merge (fast-forward into the parent and remove), or abandon (delete tree and branch)."
---

`exit_worktree` is the cleanup half of the worktree pair. Each `action` resolves the worktree differently:

| Action | Effect |
|---|---|
| `remove` | `git worktree remove <path>`. Branch is kept; tree is deleted. |
| `merge` | Switch to the worktree's parent branch, fast-forward merge the worktree branch, then `remove`. |
| `abandon` | `remove` + `git branch -D <name>`. Both tree and branch are gone. |

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `worktree_path` | string | yes | Filesystem path of the worktree to operate on. |
| `action` | string | yes | `remove`, `merge`, or `abandon`. |

## Example invocation

```json
{"worktree_path": "/Users/me/dev/chimera-spike-redis-backend", "action": "merge"}
```

```python
from chimera.tools.worktree_tool import ExitWorktreeTool

tool = ExitWorktreeTool()
result = tool.execute(
    {"worktree_path": "/Users/me/dev/chimera-spike-redis-backend",
     "action": "abandon"},
    env=local_env,
)
```

## Output sample

```
Abandoned worktree '/Users/me/dev/chimera-spike-redis-backend' (deleted tree, deleted branch 'spike-redis-backend').
```

## Notes

- `merge` refuses to run if the worktree has uncommitted changes; commit (or `remove` and accept the loss) first.
- `abandon` is destructive. Behind the [`Permissions`](/chimera/concepts/permissions/) layer it should require explicit approval.

## See also

- [`enter_worktree`](./enter-worktree.md).

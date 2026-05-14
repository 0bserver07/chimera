---
title: "enter_worktree — create an isolated git worktree"
description: "Create a new git worktree on a fresh branch, returning the worktree path. Useful for parallel work that should not touch the main checkout."
---

`enter_worktree` is the agent-facing wrapper around `git worktree add`. It creates a fresh branch and a sibling checkout at `<repo>/../<repo>-<name>/` so the agent can experiment without disturbing the main tree. Pair with [`exit_worktree`](./exit-worktree.md) to clean up.

## Schema

| Arg | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | yes | — | Branch name. Also used as the worktree directory leaf. |
| `base_branch` | string | no | `HEAD` | Branch / commit to base the new worktree on. |

## Example invocation

```json
{"name": "feature-X-experiment", "base_branch": "master"}
```

```python
from chimera.tools.worktree_tool import EnterWorktreeTool

tool = EnterWorktreeTool()
result = tool.execute(
    {"name": "spike-redis-backend"},
    env=local_env,
)
print(result.output)
```

## Output sample

```
Created worktree at /Users/me/dev/chimera-spike-redis-backend on branch 'spike-redis-backend' (from HEAD).
```

`result.metadata["worktree_path"]` carries the absolute path so a downstream tool can `cd` into it.

## Errors

| Message | Cause |
|---|---|
| `Not inside a git repository` | The env's `workdir` isn't a git checkout. |
| `Branch '<name>' already exists` | Pick a different name or delete the old branch first. |
| `Worktree path already exists` | Stale leftover — remove with `exit_worktree`. |

## See also

- [`exit_worktree`](./exit-worktree.md) — remove / merge / abandon.
- [`GitWorkflow`](/chimera/workflows/) — higher-level branch / commit orchestration.

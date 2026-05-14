---
title: "git — run safe git commands"
description: "Run git subcommands in the workspace. Destructive verbs (push --force, reset --hard, clean -f, branch -D) are blocked."
---

`git` is a thin wrapper around the env's shell that prepends `git` and rejects a small set of destructive patterns. The blocklist is hard-coded — the agent cannot disable it from the tool surface.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `command` | string | yes | Git subcommand and arguments, e.g. `status`, `add .`, `commit -m "msg"`. |

## Blocked patterns

| Pattern | Reason |
|---|---|
| `push --force`, `push -f` | Rewrite of shared history. |
| `reset --hard` | Drops uncommitted work without prompt. |
| `clean -f`, `clean -fd` | Deletes untracked files irreversibly. |
| `branch -D` | Force-deletes branches. |

When matched, the tool returns `error="Blocked: 'git <pattern>' is not allowed for safety."` and no command runs.

## Example invocation

```json
{"command": "status"}
```

```python
from chimera.tools.git import GitTool

tool = GitTool()
result = tool.execute({"command": "log --oneline -5"}, env=local_env)
print(result.output)
```

## Output sample

```
8df78c3 docs(teams): refresh agent-teams.md ...
8b65604 docs(ollama,inspirations): document Path A ...
2234c91 fix(shrew,stoat): respect OLLAMA_HOST ...
```

## Notes

- The `commit` verb is allowed but the higher-level [`GitWorkflow`](/chimera/workflows/) is preferred for production use; it manages branches, diffs, and commit strategies.
- For escapes from the blocklist, run `bash` directly with an explicit permission policy — there is no override.

## See also

- [`bash`](./bash.md) — raw shell.
- [`apply_patch`](./apply-patch.md) — file mutations.

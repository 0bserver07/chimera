---
title: "todo — agent-managed task list"
description: "Track multi-step work in a per-session todo list. Actions: add, complete, list."
---

`todo` gives the agent a structured place to track multi-step plans. The list is per-session and (optionally) persisted to disk. Use it when a task spans many tool calls and the model needs to keep its place across compactions.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | `add`, `complete`, or `list`. |
| `task` | string | for `add` / `complete` | Task description (`add`) or task ID (`complete`). |

## Constructor flags

| Flag | Default | Description |
|---|---|---|
| `cwd` | `None` | Project root (controls where the persistence file goes). |
| `persist` | `False` | Write the list to `<cwd>/.chimera/todos.json`. |
| `event_bus` | `None` | Emit todo-change events for telemetry. |
| `session_id` | `""` | Namespace key on the persistence file. |

## Example invocation

```json
{"action": "add", "task": "Write tests for the cancellation path"}
{"action": "list"}
{"action": "complete", "task": "1"}
```

```python
from chimera.tools.todo import TodoTool

tool = TodoTool(cwd=".", persist=True)
tool.execute({"action": "add", "task": "Refactor the loop"}, env=None)
tool.execute({"action": "add", "task": "Update docs"}, env=None)
print(tool.execute({"action": "list"}, env=None).output)
```

## Output sample

```
1. [ ] Refactor the loop
2. [ ] Update docs
```

After completion:

```
1. [x] Refactor the loop
2. [ ] Update docs
```

## See also

- [`think`](./think.md) — record one-off reasoning.
- [`dmail`](./dmail.md) — rewind context to a saved checkpoint.

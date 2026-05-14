---
title: "import_graph — module dependency queries"
description: "Query import relationships in the workspace: what a file imports, who imports a module, ranked neighbors, or a global summary."
---

`import_graph` builds an in-memory directed graph of module imports and answers four query shapes. Use it to scope a refactor (who imports `chimera.core.agent`?) or to verify isolation (does the env layer import the agent layer? It shouldn't).

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `action` | string | yes | `imports_of` / `importers_of` / `related` / `summary`. |
| `target` | string | for non-`summary` | File path (`imports_of`, `related`) or module name (`importers_of`). |
| `root` | string | no | Workspace root to scan. Defaults to env's `workdir`. |
| `max_results` | integer | no | Cap on the returned list. |

## Actions

| Action | Returns |
|---|---|
| `imports_of` | Modules imported by `target` file. |
| `importers_of` | Files that import `target` module. |
| `related` | Both directions, ranked by edge count. |
| `summary` | Total files, total edges, top hubs. |

## Example invocation

```json
{"action": "importers_of", "target": "chimera.core.agent"}
```

```python
from chimera.tools.import_graph import ImportGraphTool

tool = ImportGraphTool()
result = tool.execute(
    {"action": "summary", "root": "chimera"},
    env=local_env,
)
print(result.output)
```

## Output sample

```
imports_of chimera/core/agent.py:
  chimera.core.context
  chimera.core.loop
  chimera.providers.base
  chimera.types
```

```
summary (root=chimera):
  files: 412
  edges: 1,847
  top hubs (in-degree):
    chimera.types                118
    chimera.providers.base        72
    chimera.core.tool             58
```

## Notes

- Python only. JavaScript / TypeScript graph support is on the roadmap.
- Cache is in-memory per tool instance — re-instantiate to pick up new files.

## See also

- [`repo_map`](./repo-map.md) — structural map (classes, signatures).
- [`search`](./search.md), [`list_files`](./list-files.md).

---
title: "list_files — list files in a directory"
description: "List files in a directory, optionally filtered by glob pattern. Recursive."
---

`list_files` walks the environment recursively and returns matching paths, one per line. With no args, it lists everything under the current directory.

## Schema

| Arg | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | string | no | `.` | Directory to list. |
| `glob` | string | no | `null` | Glob filter applied to filenames, e.g. `*.py`. |

## Example invocation

```json
{"path": "chimera/tools", "glob": "*.py"}
```

```python
from chimera.tools.list_files import ListFilesTool

tool = ListFilesTool()
result = tool.execute({"path": "examples", "glob": "*.py"}, env=local_env)
```

## Output sample

```
examples/agent/coding_agent.py
examples/agent/coding_agent_minimal.py
examples/badger_quickstart.py
...
```

If nothing matches, the output is `No files found.`.

## See also

- [`search`](./search.md) — content search.
- [`repo_map`](./repo-map.md) — structural map.

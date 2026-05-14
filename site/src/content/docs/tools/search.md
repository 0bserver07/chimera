---
title: "search — regex search across files"
description: "Search for a regex pattern across files. Returns matching lines with file paths and line numbers, optionally filtered by glob."
---

`search` runs a regex across the environment's files and returns matching lines in `path:lineno: line` format. Supply a `glob` filter to narrow by filename.

## Schema

| Arg | Type | Required | Default | Description |
|---|---|---|---|---|
| `pattern` | string | yes | — | Regex pattern (Python `re` flavor). |
| `path` | string | no | `.` | File or directory to search in. |
| `glob` | string | no | `null` | Glob filter applied to the filename, e.g. `*.py`. |

## Example invocation

```json
{"pattern": "class .+Tool\\(BaseTool\\):", "path": "chimera/tools", "glob": "*.py"}
```

```python
from chimera.tools.search import SearchTool

tool = SearchTool()
result = tool.execute(
    {"pattern": "TODO\\(", "path": ".", "glob": "*.md"},
    env=local_env,
)
print(result.output)
```

## Output sample

```
chimera/tools/bash.py:14: class BashTool(BaseTool):
chimera/tools/edit.py:12: class EditFileTool(BaseTool):
chimera/tools/git.py:18: class GitTool(BaseTool):
```

An invalid regex returns `error="Invalid regex: ..."` and an empty `output`.

## See also

- [`list_files`](./list-files.md) — directory listing without regex.
- [`repo_map`](./repo-map.md) — structural map (classes, signatures).
- [`import_graph`](./import-graph.md) — dependency graph.

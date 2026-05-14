---
title: "repo_map — structural map of the codebase"
description: "Walk the workspace and emit files, classes, and function signatures so the agent can orient before editing."
---

`repo_map` generates a structural overview of the workspace: directory tree, top-level classes, and function signatures. Use it before broad refactors — it's cheaper than `read_file`-ing every candidate.

## Schema

| Arg | Type | Required | Default | Description |
|---|---|---|---|---|
| `path` | string | no | `.` | Directory to map. Resolved against the env's `workdir`. |
| `max_depth` | integer | no | (unlimited) | Maximum directory depth to traverse. |

## Example invocation

```json
{"path": "chimera/tools", "max_depth": 2}
```

```python
from chimera.tools.repo_map import RepoMapTool

tool = RepoMapTool()
result = tool.execute({"path": "chimera/core"}, env=local_env)
print(result.output)
```

## Output sample

```
chimera/core/
├── agent.py
│   class Agent
│     def __init__(self, provider, tools, ...)
│     def run(self, task, env)
├── loop.py
│   class ReActLoop
│     def step(self)
│     def cancel(self)
├── tool.py
│   class BaseTool
│     def execute(self, args, env)
...
```

## Notes

- Multi-language support: Python, JavaScript / TypeScript, Go, and Rust are recognised. Unknown extensions fall back to the bare filename.
- For pure import topology, prefer [`import_graph`](./import-graph.md).

## See also

- [`import_graph`](./import-graph.md) — directional dependency graph.
- [`search`](./search.md), [`list_files`](./list-files.md).

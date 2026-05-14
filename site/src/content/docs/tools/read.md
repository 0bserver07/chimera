---
title: "read_file — read a file by path"
description: "Return the contents of a file. Also marks the file as 'read' so write_guard / edit_file can later confirm it."
---

`read_file` returns the full contents of a file. It also records that the file has been read in the current session so the read-before-write guard on `edit_file` can succeed.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Relative path (resolved against the environment's `workdir`) or absolute. |

## Example invocation

```json
{"path": "chimera/core/agent.py"}
```

```python
from chimera.tools.read import ReadFileTool

tool = ReadFileTool()
result = tool.execute({"path": "README.md"}, env=local_env)
print(result.output[:200])
```

## Output sample

```
# Chimera

Compose coding agents from modular primitives.
...
```

On a missing file, `output` is empty and `error` reads `File not found: <path>`.

## See also

- [`edit_file`](./edit.md) — requires the target to have been `read_file`-d first when the read-before-write guard is on.
- [`write_file`](./write.md) — the create-only counterpart.
- [`apply_patch`](./apply-patch.md) — multi-file atomic edits.

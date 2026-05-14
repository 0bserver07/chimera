---
title: "write_file — create a new file"
description: "Write content to a new file, creating parent directories as needed. Refuses to clobber an existing file when write_guard is enforced."
---

`write_file` creates a new file at `path` and writes `content` to it. Parent directories are created automatically. When [`write_guard`](./write-guard.md) is enforced, the call is refused if `path` already exists — the agent should use [`edit_file`](./edit.md) or [`apply_patch`](./apply-patch.md) instead.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Relative or absolute path to the new file. |
| `content` | string | yes | File body. May be empty. |

## Example invocation

```json
{"path": "src/new_module.py", "content": "def hello():\n    return 'hi'\n"}
```

```python
from chimera.tools.write import WriteFileTool

tool = WriteFileTool()
result = tool.execute(
    {"path": "examples/note.txt", "content": "first line\n"},
    env=local_env,
)
```

## Output sample

```
Wrote 11 bytes to examples/note.txt
```

On a guarded clobber attempt:

```
write_file invariant violated for 'examples/note.txt':
  file already exists; use 'edit_file' or 'apply_patch'
  ...
```

## See also

- [`edit_file`](./edit.md) — patch an existing file.
- [`apply_patch`](./apply-patch.md) — atomic multi-file edits.
- [`write_guard`](./write-guard.md) — the invariant.

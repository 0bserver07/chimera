---
title: "edit_file — exact-string patch on an existing file"
description: "Replace a single exact occurrence of old_string with new_string in an existing file. Pair with read_file to satisfy the read-before-write guard."
---

`edit_file` replaces one exact occurrence of `old_string` with `new_string` in an existing file. `old_string` must appear **exactly once** — otherwise the call fails. Pair it with [`read_file`](./read.md) so the read-before-write guard (#130) is satisfied.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Relative or absolute path. The file must exist. |
| `old_string` | string | yes | Exact substring to find. Must occur exactly once. |
| `new_string` | string | yes | Replacement substring. |

## Example invocation

```json
{
  "path": "README.md",
  "old_string": "# Chimera\n",
  "new_string": "# Chimera (new)\n"
}
```

```python
from chimera.tools.edit import EditFileTool

EditFileTool.mark_file_read("/abs/path/to/README.md")  # bookkeeping
tool = EditFileTool()
result = tool.execute(
    {"path": "README.md",
     "old_string": "# Chimera\n",
     "new_string": "# Chimera (new)\n"},
    env=local_env,
)
```

## Output sample

```
Edited README.md: replaced 8 chars with 14 chars.
```

## Common errors

| Message | Cause |
|---|---|
| `old_string not found in <path>` | The substring isn't in the file. |
| `old_string is not unique in <path> (matches N times)` | Make the snippet larger. |
| `read-before-write violation: '<path>' was not read this session` | Call `read_file` first, or disable the guard. |
| `edit_file invariant violated ...` | The file doesn't exist; use `write_file`. |

## See also

- [`apply_patch`](./apply-patch.md) — multi-file atomic edits with hunks.
- [`replace_in_file`](./replace-in-file.md) — regex / multi-match variant.
- [`write_guard`](./write-guard.md) — the pre-execution invariant.

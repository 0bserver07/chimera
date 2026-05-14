---
title: "replace_in_file — regex replace in a single file"
description: "Replace every match of a regex pattern in a file with a replacement string. Supports backreferences."
---

`replace_in_file` runs `re.subn(pattern, replacement, contents)` over a single file and writes the result back. Unlike [`edit_file`](./edit.md), it tolerates multiple matches; unlike [`apply_patch`](./apply-patch.md), it works on one file at a time.

## Schema

| Arg | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Relative or absolute path. The file must exist. |
| `pattern` | string | yes | Regex pattern (Python `re` flavor). |
| `replacement` | string | yes | Replacement string. Supports `\1`, `\2`, … backreferences. |

## Example invocation

```json
{
  "path": "src/app.py",
  "pattern": "DEBUG\\s*=\\s*True",
  "replacement": "DEBUG = False"
}
```

```python
from chimera.tools.replace_in_file import ReplaceInFileTool

tool = ReplaceInFileTool()
result = tool.execute(
    {"path": "docs/changelog.md",
     "pattern": r"v(\d+)\.(\d+)\.0",
     "replacement": r"v\1.\2.1"},
    env=local_env,
)
```

## Output sample

```
3 replacements made in docs/changelog.md
```

If the pattern matches nothing:

```
0 replacements made in docs/changelog.md
```

(no error — `count == 0` is just reported).

## See also

- [`edit_file`](./edit.md) — exact-string, single-occurrence variant.
- [`apply_patch`](./apply-patch.md) — multi-file atomic edits.

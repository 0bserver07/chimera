---
title: "apply_patch — atomic multi-file patches"
description: "The structured-patch DSL for atomic multi-file edits, with rollback on filesystem error."
---

`apply_patch` mutates one or more files in a single atomic step. It accepts a self-contained patch envelope, validates every hunk before any write hits disk, and rolls back to the original tree if a filesystem error interrupts the apply mid-stream.

The end state is therefore "every file changed" or "no file changed". There is no partial-apply outcome.

## When to use it

- You need to update, create, and/or delete several files in one logical step (e.g. introducing a module that needs a corresponding test).
- You want hunk-level pre-validation so the agent surfaces a conflict before any write happens, instead of a half-done refactor.
- You want rollback on `OSError` mid-apply (disk full, permissions flip, etc.).

For single-file edits, prefer `edit_file` or `replace_in_file`. For one new file, prefer `write_file`. `apply_patch` shines when the change spans multiple files or mixes operations.

## Envelope DSL

```
*** Begin Patch
*** Update File: relative/path.py
 def hello():
-    return "old"
+    return "new"
*** Add File: docs/new.md
+# Title
+Body line.
*** Delete File: legacy/dead.py
*** End Patch
```

Markers:

| Marker | Meaning |
|---|---|
| `*** Begin Patch` / `*** End Patch` | Required envelope. |
| `*** Update File: <path>` | Modify an existing file. Hunks use unified-diff line prefixes (`" "` context, `"-"` remove, `"+"` add). |
| `*** Add File: <path>` | Create a new file. Body is `+`-prefixed lines joined by newlines. |
| `*** Delete File: <path>` | Remove an existing file. |

Paths are resolved against the current working directory. Duplicate hunks for the same path are rejected.

## Apply semantics

1. **Parse** the envelope into per-file hunks.
2. **Pre-validate** every operation:
   - `Add` target must not exist.
   - `Update` and `Delete` targets must exist as regular files.
   - Every `Update` hunk must locate its match in the current contents.
   If any check fails, no file is touched and the tool returns an error describing the problem.
3. **Apply** sequentially. Each `Add` creates parent directories as needed; each `Delete` calls `unlink`; each `Update` writes the new contents.
4. **Roll back** on `OSError` mid-apply: newly-added files are unlinked, deleted files are recreated from the in-memory snapshot, and updated files are restored. The error message reports how many prior changes were reverted.

## Prerequisites

- Chimera installed: `pip install chimera-run`
- Python 3.11+
- A working directory the agent can read and write

## Usage

### From an agent

```python
from chimera.core.agent import Agent
from chimera.providers.factory import create_provider
from chimera.tools.apply_patch import ApplyPatchTool
from chimera.env.local import LocalEnvironment

provider = create_provider(model="glm-5")
agent = Agent(provider=provider, tools=[ApplyPatchTool()])

with LocalEnvironment(workdir="./project") as env:
    result = agent.run(
        "Add a README.md and remove legacy/dead.py in one apply_patch call.",
        env,
    )
```

### Direct invocation

```python
from chimera.tools.apply_patch import ApplyPatchTool

patch = """*** Begin Patch
*** Add File: README.md
+# My Project
+
+A small example.
*** Delete File: legacy/dead.py
*** End Patch
"""

tool = ApplyPatchTool()
result = tool.execute({"patch": patch}, env=None)
print(result.output)
# Success. Updated the following files:
# Created README.md
# Deleted legacy/dead.py
```

## Error messages you might see

| Message | Cause |
|---|---|
| `apply_patch: 'patch' must be a non-empty string` | Empty or non-string `patch` argument. |
| `apply_patch parse error: ...` | Malformed envelope (missing `*** End Patch`, bad hunk header). |
| `apply_patch: cannot add existing file '<path>'` | `Add File` target already exists. |
| `apply_patch: cannot update missing file '<path>'` | `Update File` target does not exist. |
| `apply_patch: hunk conflict in '<path>': ...` | A `-` / context line in the hunk does not match current contents. |
| `apply_patch: filesystem error mid-apply on '<path>': ...; rolled back N prior change(s)` | An `OSError` interrupted the apply; the rollback message reports how many earlier changes were reverted. |

## Implementation

`ApplyPatchTool` lives in [`chimera/tools/apply_patch.py`](https://github.com/0bserver07/chimera/blob/master/chimera/tools/apply_patch.py). The line-level diff math is delegated to `chimera.core.patch_parser.PatchParser`, a pure-Python primitive owned by Chimera. This module owns the multi-file orchestration, validation, and rollback layer.

## See also

- [`write_guard`](./write-guard.md) — the pre-execution invariant that pairs `write_file` and `edit_file` with the right intent.
- [Tools](/chimera/concepts/tools/) — the broader tools concept page.

---
title: "write_guard — write_file vs edit_file invariant"
description: "Pre-execution invariant that catches write_file-on-existing and edit_file-on-missing before they corrupt or fail."
---

`write_guard` enforces a tight invariant on the two file-mutation tools:

- `write_file` **creates** a new file. The target must **not** exist.
- `edit_file` **patches** an existing file. The target **must** exist.

When the guard is enabled, a violation is caught **before** the write lands, and the agent gets a focused suggestion to use the other tool instead.

## Why

Across the polyglot benchmark suite, agents pick the wrong mutation tool roughly 57% of the time. `write_file` lands on an existing file (clobbering it instead of patching), or `edit_file` targets a non-existent file (failing with a confusing "string not found" error). `write_guard` tightens that surface so the agent gets a cheap, unambiguous hint and recovers in one round-trip instead of three.

## Default state: disabled

The guard is **off by default** so it never silently changes behaviour for callers who haven't opted in. Enable it explicitly via the agent surface or via the Python API.

## Surface

| Symbol | Purpose |
|---|---|
| `WriteGuardError` | Raised by `check_write` / `check_edit`. Carries the offending tool name, path, and a corrected suggestion. |
| `WriteGuard.set_enforced(enabled)` | Process-wide on/off switch. |
| `WriteGuard.is_enforced()` | Read the current state. |
| `WriteGuard.check_write(path, env)` | Raises if `write_file` would clobber an existing file. |
| `WriteGuard.check_edit(path, env)` | Raises if `edit_file` would target a missing file. |
| `WriteGuardTool` | Agent-facing tool (`action="check_write" / "check_edit" / "enable" / "disable" / "status"`). |

`WriteFileTool` and `EditFileTool` consult the guard automatically when enforcement is on.

## Prerequisites

- Chimera installed: `pip install chimera-run`
- Python 3.11+

## Enabling the guard

### From Python

```python
from chimera.tools.write_guard import WriteGuard

WriteGuard.set_enforced(True)
# now write_file / edit_file consult check_write / check_edit
```

### From an agent (via the tool)

```python
from chimera.core.agent import Agent
from chimera.providers.factory import create_provider
from chimera.tools.write_guard import WriteGuardTool

provider = create_provider(model="glm-5")
agent = Agent(provider=provider, tools=[WriteGuardTool()])
```

The agent can then call:

```json
{"action": "enable"}
{"action": "status"}
{"action": "check_write", "path": "src/new_module.py"}
{"action": "check_edit",  "path": "src/existing.py"}
{"action": "disable"}
```

## What the agent sees on a violation

```text
write_file invariant violated for 'src/existing.py':
  file already exists; use 'edit_file' or 'apply_patch'
  to modify 'src/existing.py' instead of overwriting it
  with write_file
```

```text
edit_file invariant violated for 'src/new_module.py':
  file does not exist; use 'write_file' to create
  'src/new_module.py' instead of editing it with edit_file
```

The message is short on purpose — the model can self-correct in one turn instead of looping on the underlying error.

## Pairing with `apply_patch`

For multi-file changes, `apply_patch` already enforces the right per-file invariant: an `Add File` hunk fails if the target exists, an `Update File` hunk fails if the target is missing. `write_guard` covers the same shape for the simpler `write_file` and `edit_file` calls.

## Reset between tests

```python
WriteGuard.reset()  # restores the default-disabled state
```

Test fixtures call this between cases so guard state from one test never leaks into the next.

## Implementation

[`chimera/tools/write_guard.py`](https://github.com/0bserver07/chimera/blob/master/chimera/tools/write_guard.py) — the guard, the error, and the agent-facing tool. The guard's class-level `_enforced` flag is process-wide; callers in concurrent test runs that need per-suite isolation should swap in their own subclass.

## See also

- [`apply_patch`](./apply-patch.md) — atomic multi-file patches with the same per-file invariant baked in.

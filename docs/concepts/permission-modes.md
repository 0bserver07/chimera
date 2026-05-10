---
title: "Permission Modes"
description: "The five standard approval modes (read-only / suggest / auto / yolo / strict) exposed via --permission-mode."
---

Chimera ships a 5-mode approval surface that controls how each tool call is decided: **allowed**, **denied**, or **asked**. The same five modes are wired into the `ferret`, `badger`, and `mink` CLIs as `--permission-mode`, and into Python via `chimera.permissions.modes.ApprovalMode`.

The modes are ordered from least permissive to most permissive (with `STRICT` as the cautious outlier that asks for everything):

| Mode | Reads | Edits | Bash / git / network | Defaults pair well with |
|---|---|---|---|---|
| `read-only` | allow | **deny** | **deny** | Plan or review runs against a sandbox. |
| `suggest` | allow | ask | ask | Day-to-day interactive work. |
| `auto` | allow | allow | ask | Workspace-write sandboxes. |
| `yolo` | allow | allow | allow | A throwaway docker container. |
| `strict` | ask | ask | ask | High-risk environments where even reads need confirmation. |

## When to pick which

- **`read-only`** — only the read whitelist (`read_file`, `search`, `list_files`, `repo_map`) is allowed; every side-effecting tool is denied outright. Useful for plan/review runs.
- **`suggest`** — reads auto-approve; every write/edit/bash/git call is surfaced for explicit approval. Closest to "show your work before acting".
- **`auto`** — reads + simple edits auto-approve; bash/git/destructive ops still ask. The default for sandboxed editing sessions.
- **`yolo`** — every tool call auto-approves. Use only inside a sandbox.
- **`strict`** — every tool call (including reads) is surfaced for explicit approval. Pair with a non-interactive deny fallback for fully unattended runs.

## CLI usage

```bash
ferret run --permission-mode read-only "Audit src/ for security issues"
badger eval --permission-mode auto humaneval --limit 10
mink chat --permission-mode strict
```

The flag accepts the canonical spellings above plus a few aliases for backwards compatibility:

| Alias | Maps to |
|---|---|
| `read_only` / `readonly` | `read-only` |
| `full` (legacy ferret `--approval`) | `yolo` |
| `default` (legacy mink) | `suggest` |
| `acceptEdits` / `accept-edits` | `auto` |
| `bypassPermissions` / `bypass-permissions` | `yolo` |
| `plan` (legacy mink) | `read-only` |

## Python usage

```python
from chimera.permissions.modes import ApprovalMode, parse_mode, policy_for_mode

# Pick a mode
mode = ApprovalMode.SUGGEST

# Or parse from a string
mode = parse_mode("auto")

# Get the matching PermissionPolicy
policy = policy_for_mode(mode)

# Wire into the loop
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.permissions.checker import PermissionChecker

checker = PermissionChecker(policy=policy)
loop = ReAct(config=LoopConfig(permission_checker=checker))
```

## Mapping to PermissionPolicy

`policy_for_mode` returns the right `PermissionPolicy` for each mode:

| Mode | Policy |
|---|---|
| `read-only` | `ReadOnly` (allow `read_file`, `search`, `list_files`, `repo_map`; deny everything else) |
| `suggest` | `Interactive` (allow reads; ask on writes/exec) |
| `auto` | `AutoEditPolicy` (allow reads + simple edits; ask on bash/git/destructive) |
| `yolo` | `AutoApprove` (allow everything) |
| `strict` | `AlwaysAskPolicy` (ask on every call, including reads) |

A new policy instance is returned per call, so any per-policy mutable state stays per-CLI-invocation.

## Relationship to the legacy `PermissionMode`

The older 6-value enum `PermissionMode` (`default` / `plan` / `accept_edits` / `bypass_permissions` / `dont_ask` / `auto`) is still used by the interactive REPL and the in-process permission checker. The 5-mode `ApprovalMode` is the **CLI-surface** enum; `parse_mode` and `policy_for_mode` translate between them so a CLI flag and a Python policy stay in sync.

## Prerequisites

- Chimera installed: `pip install chimera-run`
- Python 3.11+

## Where modes live

- Enum: `chimera.permissions.modes.ApprovalMode`
- Parser: `chimera.permissions.modes.parse_mode(value)`
- Policy lookup: `chimera.permissions.modes.policy_for_mode(mode)`
- Built-in policies: `chimera.permissions.presets` (`ReadOnly`, `Interactive`, `AutoApprove`)

## See also

- [Permissions module reference](/chimera/modules/permissions/) — the underlying `PermissionPolicy` ABC and presets.
- [Hook events](./hook-events.md) — `PermissionRequest` / `PermissionDenied` / `Elicitation` fire alongside policy decisions.

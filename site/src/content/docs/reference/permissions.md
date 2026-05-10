---
title: "chimera.permissions"
description: "Reference for chimera.permissions — policies, presets, audit log, risk classifier, approval modes."
---

`chimera.permissions` decides whether a tool call may execute. Every
policy implements `evaluate(tool_name, args) -> PermissionAction`.

For a tutorial, see [Configure Permissions](/configure-permissions/).

## Policies

```python
from chimera.permissions import (
    PermissionPolicy,
    PermissionAction,
    AutoApprove, AlwaysDeny, AllowList, DenyList,
    Interactive, ReadOnly,
    PermissionRuleset, Rule,
)
```

| Symbol | Module | Purpose |
|---|---|---|
| `PermissionPolicy` | `chimera.permissions.base` | ABC. Override `evaluate(tool_name, args)`. |
| `PermissionAction` | `chimera.permissions.base` | Enum: `ALLOW`, `DENY`, `ASK`. |
| `AutoApprove` | `chimera.permissions.presets` | Approve everything. |
| `AlwaysDeny` | `chimera.permissions.presets` | Deny everything. |
| `AllowList(allowed=[...])` | `chimera.permissions.presets` | Allow named tools, deny the rest. |
| `DenyList(denied=[...])` | `chimera.permissions.presets` | Deny named tools, allow the rest. |
| `Interactive` | `chimera.permissions.presets` | Reads ALLOW; mutating tools ASK. |
| `ReadOnly` | `chimera.permissions.presets` | Only `read_file`, `search`, `list_files`, `repo_map` ALLOW. Everything else DENY. |
| `PermissionRuleset(rules=[...])` | `chimera.permissions.base` | Last-match-wins glob ruleset. |
| `Rule(tool_pattern, action, arg_key=, arg_pattern=, description=)` | `chimera.permissions.base` | Single rule. |

## Approval modes (`chimera.permissions.modes`)

The five-mode `--permission-mode` surface used by `ferret`, `badger`,
and `mink` CLIs:

| Mode | Backing policy |
|---|---|
| `read-only` | `ReadOnly` |
| `suggest` | `Interactive` |
| `auto` | `AutoEditPolicy` (reads + simple edits ALLOW; bash/git ASK) |
| `yolo` | `AutoApprove` |
| `strict` | `AlwaysAskPolicy` (every call ASKs) |

```python
from chimera.permissions.modes import (
    ApprovalMode,
    parse_mode,
    policy_for_mode,
    AutoEditPolicy,
    AlwaysAskPolicy,
)

policy = policy_for_mode("auto")  # AutoEditPolicy()
```

`parse_mode()` accepts canonical spellings, underscore variants, the
legacy ferret `--approval` values (`full` → `yolo`), and the legacy mink
`--permission-mode` choices (`default`, `acceptEdits`, `bypassPermissions`,
`plan`).

The legacy six-mode `PermissionMode` enum (`default`, `plan`,
`accept_edits`, `bypass_permissions`, `dont_ask`, `auto`) lives in the
same module for backwards compatibility with the in-process permission
checker.

## Audit log (`chimera.permissions.audit`)

```python
from chimera.permissions.audit import AuditEntry, AuditLog
```

`AuditLog.record(entry)` appends a `(tool_name, args, action, granted,
timestamp)` row; `summary()`, `for_tool(name)`, and `clear()` query
or reset the log.

## Risk classifier (`chimera.permissions.risk`)

```python
from chimera.permissions.risk import RiskLevel, classify_risk
```

`classify_risk(bash_command)` returns `LOW` / `MEDIUM` / `HIGH` based on
known-dangerous bash patterns (`rm -rf`, `dd if=`, `chmod 777`, ...).
Called by `RuleBasedSecurityAnalyzer`.

## See also

- [Configure Permissions](/configure-permissions/) for tutorial setup.
- [Add Security Policies](/add-security-policies/) for `SecurityRisk`
  layered on top of permissions.
- [`chimera.core`](/reference/core/) for `LoopConfig.permissions=`.

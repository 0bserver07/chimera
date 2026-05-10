---
title: "Permissions"
description: "Permissions"
---

`chimera.permissions` provides a rule-based system that decides whether each
tool invocation should be allowed, denied, or require user confirmation.  It
ships with five presets covering common use cases and a `PermissionRuleset`
for fine-grained control.

## Core types

### PermissionAction (enum)

Every permission evaluation returns one of three outcomes:

| Value | Meaning |
|-------|---------|
| `ALLOW` | Tool call proceeds without user input |
| `DENY` | Tool call is blocked |
| `ASK` | User is prompted for confirmation |

### PermissionPolicy (ABC)

The interface that every policy must implement:

```python
class PermissionPolicy(ABC):
    @abstractmethod
    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction: ...
```

## Rule and PermissionRuleset

### Rule dataclass

A single matching rule with glob-based patterns:

| Field | Type | Description |
|-------|------|-------------|
| `tool_pattern` | `str` | Glob pattern for the tool name (e.g. `"bash"`, `"write_*"`, `"*"`) |
| `action` | `PermissionAction` | Action to apply when matched |
| `arg_key` | `str \| None` | Optional argument key to inspect |
| `arg_pattern` | `str \| None` | Glob pattern for `args[arg_key]` |
| `description` | `str` | Human-readable description |

### PermissionRuleset

An ordered list of `Rule` objects evaluated with **last-match-wins** semantics
(similar to `.gitignore`).  Pattern matching uses `fnmatch.fnmatch`.

```python
from chimera.permissions import PermissionRuleset, Rule, PermissionAction

policy = PermissionRuleset(
    rules=[
        Rule(tool_pattern="*", action=PermissionAction.ASK),
        Rule(tool_pattern="read_file", action=PermissionAction.ALLOW),
        Rule(tool_pattern="search", action=PermissionAction.ALLOW),
        Rule(
            tool_pattern="bash",
            action=PermissionAction.DENY,
            arg_key="command",
            arg_pattern="rm *",
            description="Block destructive shell commands",
        ),
    ],
    default=PermissionAction.ASK,
)

action = policy.evaluate("bash", {"command": "rm -rf /"})
# -> PermissionAction.DENY (last matching rule wins)
```

## Evaluation flow

```mermaid
flowchart TD
    TC[Tool Call] --> ITER[Iterate rules in order]
    ITER --> MATCH{Tool pattern matches?}
    MATCH -- No --> NEXT[Next rule]
    MATCH -- Yes --> ARG{Has arg constraint?}
    ARG -- No --> SAVE[Save as last match]
    ARG -- Yes --> ARGM{Arg matches?}
    ARGM -- Yes --> SAVE
    ARGM -- No --> NEXT
    SAVE --> NEXT
    NEXT --> MORE{More rules?}
    MORE -- Yes --> MATCH
    MORE -- No --> RET[Return last match or default]
```

## Presets

Five convenience policies cover the most common scenarios:

| Preset | Behaviour |
|--------|-----------|
| `AutoApprove` | Allow everything unconditionally |
| `AlwaysDeny` | Deny everything unconditionally |
| `AllowList(allowed)` | Allow only named tools; deny all others |
| `ReadOnly` | Allow `read_file`, `search`, `list_files`, `repo_map`; deny the rest |
| `Interactive` | Auto-allow reads; prompt for `bash`, `write_file`, `edit_file`, `replace_in_file`, `git` |

```python
from chimera.permissions import ReadOnly, Interactive

# Read-only agent
policy = ReadOnly()
policy.evaluate("read_file", {})   # ALLOW
policy.evaluate("bash", {})        # DENY

# Interactive confirmation for writes
policy = Interactive()
policy.evaluate("read_file", {})   # ALLOW
policy.evaluate("write_file", {})  # ASK
```

## Custom policies

Implement `PermissionPolicy` for domain-specific logic:

```python
from chimera.permissions import PermissionPolicy, PermissionAction

class TimeBasedPolicy(PermissionPolicy):
    """Deny writes outside business hours."""
    def evaluate(self, tool_name, args):
        import datetime
        hour = datetime.datetime.now().hour
        if tool_name.startswith("write") and not (9 <= hour < 17):
            return PermissionAction.DENY
        return PermissionAction.ALLOW
```

## ApprovalMode (5-mode surface)

`ApprovalMode` is the standard 5-mode surface that several CLIs expose
through a `--permission-mode` flag.  Each mode resolves to a concrete
policy via `policy_for_mode()`:

| Mode value | Policy | Behaviour |
|------------|--------|-----------|
| `read-only` | `ReadOnly` | Only the read whitelist allowed; everything else denied |
| `suggest` | `Interactive` | Reads auto-approve; writes/bash/git surface for explicit approval |
| `auto` | `AutoEditPolicy` | Reads + simple file edits auto-approve; bash/git/destructive ops `ASK` |
| `yolo` | `AutoApprove` | Every tool call auto-approves (sandbox-only) |
| `strict` | `AlwaysAskPolicy` | Every tool call (including reads) requires explicit approval |

```python
from chimera.permissions import ApprovalMode, parse_mode, policy_for_mode

# Direct construction
policy = policy_for_mode(ApprovalMode.AUTO)

# Parse from a CLI string (case-insensitive, accepts aliases)
mode = parse_mode("read-only")        # ApprovalMode.READ_ONLY
mode = parse_mode("acceptEdits")      # ApprovalMode.AUTO  (legacy alias)
mode = parse_mode("bypassPermissions") # ApprovalMode.YOLO (legacy alias)

policy = policy_for_mode(mode)
policy.evaluate("bash", {"command": "ls"})  # PermissionAction.ASK
```

`parse_mode()` accepts canonical spellings (`read-only`, `suggest`,
`auto`, `yolo`, `strict`), underscore variants (`read_only`), and the
legacy `--approval` / `--permission-mode` strings (`default`,
`acceptEdits`, `bypassPermissions`, `plan`, `full`).

### Legacy `PermissionMode` enum

The pre-G3 six-mode `PermissionMode` enum
(`DEFAULT`/`PLAN`/`ACCEPT_EDITS`/`BYPASS`/`DONT_ASK`/`AUTO`) is still
exported for backwards compatibility with the interactive REPL and the
in-process permission checker.  New code should prefer `ApprovalMode`.

## Audit log

`AuditLog` records every permission evaluation for after-the-fact
inspection.  Each entry is an `AuditEntry` with `tool_name`, `action`,
`granted`, `args`, and a timestamp.

```python
from chimera.permissions import AuditLog

log = AuditLog()
log.record(tool_name="bash", action="bash:rm /tmp/x", granted=False, args={"command": "rm /tmp/x"})

log.summary()        # {"total": 1, "granted": 0, "denied": 1, ...}
log.for_tool("bash") # list of entries for the bash tool
log.clear()
```

## Risk classification

`classify_risk()` maps bash commands to a `RiskLevel` (`SAFE`,
`MODERATE`, `DESTRUCTIVE`, `CRITICAL`) so callers can surface elevated
risk to the user before granting `ASK` decisions.

```python
from chimera.permissions import classify_risk, format_risk

level = classify_risk("rm -rf /")          # RiskLevel.CRITICAL
banner = format_risk(level)                # human-readable summary
```

---
title: Otter Permissions
description: Declarative permission rules for chimera otter — JSON-on-disk, fnmatch patterns, last-match-wins evaluation.
---

# Otter Permissions

`chimera otter` honors declarative permission rules persisted at
`~/.chimera/permissions.json`. Each rule maps a tool name (and an
optional argument pattern) to one of `allow`, `deny`, or `ask`. The
file is read once at REPL / server startup and any changes via the
`/permissions` slash command are flushed back to disk immediately.

## File format

```json
{
  "version": 1,
  "default": "ask",
  "rules": [
    {"tool": "read_file", "action": "allow"},
    {"tool": "search",    "action": "allow"},
    {
      "tool": "bash",
      "action": "deny",
      "arg_key": "command",
      "arg_pattern": "rm -rf*",
      "description": "block destructive recursive deletes"
    },
    {"tool": "*", "action": "ask"}
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `version` | no | Schema version. Bumped only when the JSON shape changes. Forward-compat: unknown versions warn and load anyway. |
| `default` | no | Action returned when no rule matches. Defaults to `ask`. |
| `rules` | no | Ordered list of rule objects. Empty list ≡ "default everywhere". |
| `rules[*].tool` | **yes** | fnmatch glob for the tool name. `*` matches every tool. |
| `rules[*].action` | **yes** | One of `allow`, `deny`, `ask` (or aliases — `permit`, `block`, `prompt`, `confirm`). |
| `rules[*].arg_key` | no | Key into the tool's argument dict to inspect alongside the tool name. |
| `rules[*].arg_pattern` | no | fnmatch glob applied to `str(args[arg_key])`. Both `arg_key` and `arg_pattern` must be set together for argument scoping to apply. |
| `rules[*].description` | no | Human-readable note rendered by `/permissions list`. |

### Evaluation semantics

Rules are scanned in order; the **last matching rule wins** (mirrors
`.gitignore`). Argument-scoped rules only fire when `arg_key` is
present in the tool's arguments **and** `args[arg_key]` globs
`arg_pattern`. Otherwise the rule is skipped (it does not "fail closed").

## CLI: `/permissions` slash command

| Command | Behavior |
|---|---|
| `/permissions` (or `/permissions list`) | Print the current rules with their 0-based index. |
| `/permissions add <tool> <action> [arg_key=PAT] [-- description]` | Append a rule. The `arg_key=PAT` shorthand attaches an argument pattern; anything after a literal `--` token becomes the description. |
| `/permissions remove <index>` | Drop the rule at *index*. |

Examples:

```
/permissions add read_file allow
/permissions add bash deny command="rm -rf*" -- block destructive recursive deletes
/permissions remove 0
```

## Common patterns

### Read-only mode

Deny everything, then allow only the safe reads:

```json
{
  "default": "deny",
  "rules": [
    {"tool": "read_file",  "action": "allow"},
    {"tool": "search",     "action": "allow"},
    {"tool": "list_files", "action": "allow"},
    {"tool": "repo_map",   "action": "allow"}
  ]
}
```

### Auto-approve everything except dangerous bash

```json
{
  "default": "allow",
  "rules": [
    {
      "tool": "bash",
      "action": "deny",
      "arg_key": "command",
      "arg_pattern": "rm -rf*"
    },
    {
      "tool": "bash",
      "action": "ask",
      "arg_key": "command",
      "arg_pattern": "sudo*"
    }
  ]
}
```

### Wildcard fall-through to ask

```json
{
  "default": "deny",
  "rules": [
    {"tool": "read_file", "action": "allow"},
    {"tool": "*",         "action": "ask"}
  ]
}
```

The wildcard rule moves the default from "deny" to "ask" for any tool
the explicit list didn't allow — useful when you want a low-risk
prompt rather than an outright block.

## Programmatic API

```python
from pathlib import Path
from chimera.otter import permission_rules as pr

# Load + build a policy ready for LoopConfig.permission_policy.
ruleset = pr.load_permission_rules()           # reads ~/.chimera/permissions.json
policy = pr.build_policy(ruleset)              # PermissionRuleset

# Mutate.
pr.add_rule(pr.OtterPermissionRule(tool="bash", action="deny"))
removed = pr.remove_rule(0)
rules = pr.list_rules()

# Custom location (CI / sandbox).
import os
os.environ["CHIMERA_PERMISSIONS_FILE"] = "/tmp/perms.json"
```

## Storage location

| Variable | Value |
|---|---|
| Default file | `~/.chimera/permissions.json` |
| Override env var | `CHIMERA_PERMISSIONS_FILE` |
| Created on first save | Yes (parent dir auto-created) |

## Error handling

The loader is intentionally permissive:

* **Missing file** → empty ruleset (no warning).
* **Malformed JSON** → empty ruleset + logged warning.
* **Bad action / missing required field on a single rule** → skipped + logged warning; other rules still load.
* `load_permission_rules(strict=True)` flips all of the above to raise `PermissionRulesError` for callers (CI lints, `chimera doctor`) that want to fail fast.

## See also

- [`chimera/permissions/rule.py`](https://github.com/0bserver07/chimera/blob/master/chimera/permissions/rule.py) — the underlying `Rule` / `PermissionRuleset` classes used by `build_policy()`.
- [`chimera otter serve`](./server.md) — applies the policy via the existing `LoopConfig.permission_policy` slot.
- [Permission modes](../api/permissions.md) — the `--permission-mode` CLI flag (G3) composes with file rules: rules run first; the mode-derived policy is the fallback.

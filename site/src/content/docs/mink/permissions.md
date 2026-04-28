---
title: "Mink Permissions"
description: "The chimera mink permission system: settings.json allow/ask/deny rule grammar and PreToolUse hook surface."
---

# Permissions

Chimera's permission system is a drop-in target for the ecosystem
`settings.json` schema. The same `permissions.allow / ask / deny` arrays,
the same rule grammar, the same four modes, the same hook overrides.
This page is the authoritative reference for the ecosystem-compatible
surface.

Implementation entry points:

- `chimera/permissions/checker.py` — decision algorithm
- `chimera/permissions/rules.py` — `PermissionRule`, grammar parser
- `chimera/permissions/modes.py` — the four (plus two internal) modes
- `chimera/cli/permission_prompt.py` — interactive ASK prompt
- `chimera/mink/settings.py` — `settings.json` loader (M2-A)

## Modes

| Mode | Selector | Behavior |
|------|----------|----------|
| `default` | `--permission-mode default` (or unset) | Standard interactive: ask on rule miss, allow on `allow`, deny on `deny`. |
| `acceptEdits` | `--permission-mode acceptEdits` | Auto-allow file edits in the working tree; ask everything else. |
| `bypassPermissions` | `--permission-mode bypassPermissions`, `--yolo` | All tool calls allowed without prompts. Bypass-immune safety checks still apply. |
| `plan` | `/plan` slash command, `--permission-mode plan` | Read-only: every write/edit/bash returns deny. `ExitPlanMode` restores the prior mode. |

Two internal modes (`auto`, `bubble`) are used by the dispatch layer
and are not user-selectable.

A "bypass-immune" decision is one that survives `bypassPermissions`.
Three categories qualify: `requires_user_interaction`,
`content_specific_ask`, and `safety_check`. They live in steps 1e–1g of
the algorithm below.

## Rule grammar — `Tool(arg_key:pattern)`

Every rule is a string. Three legal shapes:

```
Tool                       # entire tool, any input
Tool(arg_key:pattern)      # match a specific input field
Tool(content_pattern)      # legacy: match the tool's "content" field
mcp__server                # implicit suffix: matches every tool on that MCP server
```

Examples that real CC users write:

```json
{
  "permissions": {
    "allow": [
      "Read",
      "Bash(command:git status*)",
      "Bash(command:git diff*)",
      "mcp__chimera-search"
    ],
    "ask": [
      "Bash(command:rm*)",
      "Bash(command:git push*)",
      "Edit(file_path:/etc/*)"
    ],
    "deny": [
      "Bash(command:sudo*)",
      "WebFetch",
      "Read(file_path:**/.env)"
    ]
  }
}
```

Pattern matching uses `fnmatch` against the value extracted from
`tool_input[arg_key]`. Tool-name globs (`Bash`, `mcp__*`, `Edit*`) are
also fnmatch. Parentheses inside content can be escaped with `\(` / `\)`
and a literal backslash with `\\`.

## Decision algorithm

The full sequence (`PermissionChecker.check`):

1. **Deny rules** — any matching `deny` entry → `DENY`.
2. **Security analyzer** — `SecurityAnalyzer.analyze(tool, input)` may
   short-circuit to `DENY` (e.g. obvious credential exfil) or escalate
   to `ASK`.
3. **Ask rules** — any matching `ask` entry → `ASK` (bypass-immune).
4. **Tool hook** — `tool.check_permissions(input, ctx)` may return
   `ALLOW` / `DENY` / `ASK`. ASK results from this step are
   bypass-immune.
5. **Mode** — `bypassPermissions` (and `plan` when bypass is available)
   → `ALLOW` for anything not yet decided.
6. **Allow rules** — any matching `allow` entry → `ALLOW`.
7. **Default ask** — fall through is `ASK`.

Steps 3, 4 (when ASK), and the security analyzer are collectively the
"bypass-immune" set. Even with `--yolo`, they still surface a prompt.

## The interactive prompt

When the algorithm returns `ASK`, the REPL renders a single panel and
reads one keystroke (`chimera/cli/permission_prompt.py`):

```
--------------- Permission required ---------------
Tool:   Bash
Input:  rm -rf node_modules
Risk:   medium
Reason: matches Bash(command:rm*) ask-rule
[a] Approve once   [A] Always allow this command
[d] Deny once      [D] Always deny this command
[c] Cancel turn    [?] Help
---------------------------------------------------
>
```

Six keys:

- `a` — approve this single tool call
- `A` — approve and append a rule to `~/.claude/settings.local.json`
- `d` — deny this single tool call
- `D` — deny and append a rule to `~/.claude/settings.local.json`
- `c` — raise `PermissionCancelled`; the loop aborts the current turn
- `?` — re-display the help footer (useful after a typo)

The reader uses `termios` + `tty.setcbreak` on POSIX and `msvcrt.getwch`
on Windows. When stdin is not a TTY (CI, piped input, tests) it
gracefully degrades to a single character of `readline()` so automation
keeps working. When the input dries up (EOF) the prompt fails closed
with a one-shot `DENY` rather than hanging.

`A`/`D` synthesize a rule from the most identifying input field
(`command`, `file_path`, `path`, `url`, `pattern`) — e.g. `git status`
becomes `Bash(command:git*)`. Callers may pre-supply
`PermissionRequest.rule_suggestion` to override.

`PermissionCancelled` is the only exception the prompt raises by
design; the REPL catches it, prints `cancelled`, and returns to the
input line without retrying the model.

## Hook overrides

A `PreToolUse` hook can return JSON that overrides the entire decision:

```json
{
  "hookSpecificOutput": {
    "permissionDecision": "deny",
    "permissionDecisionReason": "Bash(rm) is forbidden in this repo"
  }
}
```

Allowed values: `"allow"`, `"deny"`, `"ask"`. The hook executor
(`chimera/hooks/executor.py`) routes `permissionDecision` straight into
the resolver and short-circuits the rest of the algorithm. The reason
string is propagated into both the audit log and the prompt panel.

A second override, `updatedInput`, lets the hook rewrite the tool's
arguments before dispatch (e.g. swap `git push --force` for
`git push`). This runs after the permission decision is `allow` so
hooks cannot bypass denies via mutation.

## `settings.json` schema

The `permissions` block, with all keys optional:

```json
{
  "permissions": {
    "mode": "default",
    "allow": ["Bash(command:git*)"],
    "ask":   ["Bash(command:rm*)"],
    "deny":  ["WebFetch"],
    "additionalDirectories": ["/Users/me/extra-workspace"],
    "defaultMode": "default"
  }
}
```

Files are merged in CC's precedence order
(`policy < flag < local < project < user < cliArg < command < session`)
by `chimera/mink/settings.py`. Both `~/.claude/settings.json`
(user) and `./.claude/settings.local.json` (untracked, the file the
interactive prompt writes) are read on every launch.

## Quick reference

| Action | Code |
|--------|------|
| Build the prompt | `InteractivePermissionPrompt()` |
| Wire to a loop | `LoopConfig(permission_policy=InteractivePermissionPrompt())` |
| Programmatic ASK | `prompt.prompt(PermissionRequest(tool_name="Bash", input_args={"command": "rm -rf /"}))` |
| Catch cancel | `except PermissionCancelled:` |
| Override settings location | env `CHIMERA_MINK_SETTINGS_PATH` or `settings_path=` kwarg |

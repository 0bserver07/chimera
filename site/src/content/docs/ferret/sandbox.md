---
title: Ferret Sandbox
description: Ferret's three sandbox modes — read-only, workspace-write, workspace-write-network — and how each blocks or allows tool operations.
---

# Sandbox — what ferret can touch

Ferret is sandbox-first. Every tool call passes through a sandbox
guard before it ever reaches the underlying `Operations` layer
(`chimera/core/operations.py`). The guard is selected by a single
flag at process start, `--sandbox`, and stays fixed for the life of
the session because it controls OS-level kernel hooks.

This is the headline differentiator versus mink (which uses a
fine-grained `--allowed-tools` allowlist) and otter (which leans on
permission policies). Ferret picks one of three escalation levels
and stops asking. To change levels, exit and restart with a new
flag.

## The three modes

| Mode | File reads | File writes inside cwd | File writes outside cwd | Network from tools |
|---|---|---|---|---|
| `read-only` (default) | yes | **no** | **no** | **no** |
| `workspace-write` | yes | yes | **no** | **no** |
| `workspace-write-network` | yes | yes | **no** | yes |

There is intentionally no "full power" sandbox mode. To get unscoped
filesystem and network access, drop the sandbox (`--sandbox off`)
and run ferret in a disposable container. See "Escape hatches" below.

## `read-only` (default)

The safest possible posture and the ferret default. Use this when
you're pointing ferret at an unfamiliar repo, a prod working tree,
or a directory that contains things you don't want changed.

**Allows:**

- `Read`, `list_files`, `search`, `image_read`, `import_graph`,
  `repo_map` — every read tool.
- `Bash` for read-only commands (`ls`, `cat`, `git log`,
  `git status`, `find`, `rg`, `grep`, etc.). The risk classifier in
  `chimera/permissions/risk.py` decides what counts; commands that
  match a write-or-network heuristic are denied.

**Blocks:**

- `Write`, `Edit`, `replace_in_file` — refused at the sandbox layer
  before the `WriteOps` adapter is called.
- `Bash` commands that touch the filesystem (`rm`, `mv`, `cp`,
  `mkdir`, `touch`, `>`, `>>`, `tee`, `sed -i`, `chmod`, `chown`).
- `Bash` commands that touch the network (`curl`, `wget`, `git push`,
  `git fetch`, `npm install`, `pip install`, `apt`, etc.).
- `web_fetch`, `browser` — refused with a "sandbox=read-only" error.

The default approval preset paired with this sandbox is also
`read-only` (see [`approval.md`](approval.md)), so the user never
gets prompted; tools either run or fail with a clear sandbox error.

## `workspace-write`

The middle position. Ferret can read and write inside the current
working directory but cannot reach the network and cannot write
outside `cwd`.

**Allows everything `read-only` allows, plus:**

- `Write`, `Edit`, `replace_in_file` — when the resolved absolute
  path is inside `cwd`.
- `Bash` write commands (`rm`, `mv`, `mkdir`, `>`, etc.) — when the
  affected paths resolve inside `cwd`. The shell guard expands
  redirects and arguments before the call lands.

**Still blocks:**

- File writes whose resolved absolute path is **outside** `cwd`
  (including `~/`, `/tmp`, `/etc`, sibling repos). The check is on
  the final realpath, so symlink trickery does not bypass it.
- All network access — `curl`, `wget`, package managers, `git push`,
  `git fetch`, `web_fetch`, the `browser` tool.

**Typical use:**

```bash
chimera ferret --sandbox workspace-write --approval auto -p "fix the failing test"
```

This is the everyday "let the agent edit my repo" mode. Approval
preset `auto` (see [`approval.md`](approval.md)) skips the prompt on
each write so the loop runs end-to-end.

## `workspace-write-network`

The widest mode ferret offers without dropping the sandbox entirely.
Same filesystem rules as `workspace-write`; network is allowed.

**Allows everything `workspace-write` allows, plus:**

- `web_fetch`, the `browser` tool — outbound HTTPS.
- `Bash` network commands — `curl`, `wget`, package managers
  (`pip`, `npm`, `cargo`, `go`, `apt`, etc.), `git push`,
  `git fetch`, `git clone` (where the clone target is inside `cwd`).

**Still blocks:**

- File writes outside `cwd` — same realpath check as
  `workspace-write`.

**Typical use:**

```bash
chimera ferret --sandbox workspace-write-network --approval auto \
               -p "pip install httpx, write a smoke test, run it"
```

This is the right mode for "let the agent install a dep and use it"
work. It is also the right mode for `git push` and friends in a
trusted CI context.

## How the sandbox is enforced

The sandbox runner lives in `chimera/ferret/sandbox.py`. It wraps
the standard `LocalEnvironment` and intercepts every operation
before delegating:

1. **Read tools** — pass through unchanged.
2. **Write tools** — the resolved absolute path is checked against
   `cwd` (`os.path.commonpath([realpath, cwd]) == cwd`). Outside =
   refused with a `SandboxViolation` error.
3. **Bash** — argv is parsed (best-effort), redirects + heredocs are
   expanded, and the resulting effective paths run through the same
   realpath check. Network commands are caught by the network
   classifier in `chimera/permissions/risk.py`.
4. **Network tools** (`web_fetch`, `browser`) — refused unless the
   mode is `workspace-write-network`.

A `SandboxViolation` is logged as a `ToolResult` with a structured
error so the model can recover or ask the user to widen the sandbox.

## Composing with approval

The sandbox blocks at the **OS / runtime** layer. The approval
preset blocks at the **policy** layer. A tool call has to pass both.

| Sandbox | Approval | Net effect |
|---|---|---|
| `read-only` | `read-only` | Reads only. Never asks. |
| `read-only` | `auto` | Reads only. Approval setting is moot. |
| `workspace-write` | `read-only` | **Reads only** — approval still blocks writes. |
| `workspace-write` | `auto` | Reads + writes inside cwd, no asks. |
| `workspace-write-network` | `auto` | Reads + writes + net, no asks. |
| any | `full` | Whatever the sandbox allows; approval is a no-op. |

See [`approval.md`](approval.md) for the approval matrix.

## Escape hatches

When ferret's sandbox is too tight for a specific operation, you
have three options in increasing order of widening:

1. **Restart at a higher mode.** Quickest path; restart ferret with
   `--sandbox workspace-write` or `workspace-write-network`.
2. **Drop the sandbox** with `--sandbox off`. The session bypasses
   the sandbox guard entirely — equivalent to running ferret as a
   normal user process. Recommended only inside a disposable
   container (Docker, devcontainer, ephemeral VM).
3. **Use the `Bash(timeout=...)` tool with explicit confirmation.**
   The user can step through a sequence of dangerous commands one at
   a time with `--approval read-only`, confirming each.

## Defaults from `~/.codex/config.toml`

The optional config file (filesystem fact, see
[`security-and-trademarks.md`](security-and-trademarks.md)) can pin
a default sandbox mode:

```toml
[sandbox]
mode = "workspace-write"
```

CLI `--sandbox` always wins. The env var `FERRET_SANDBOX` is read
when neither flag nor config-file value is present.

## See also

- [`approval.md`](approval.md) — approval presets, the policy-layer
  twin of the sandbox.
- [`quickstart.md`](quickstart.md) — sandbox + approval flag examples.
- `chimera/ferret/sandbox.py` — runner implementation.
- `chimera/permissions/risk.py` — the bash risk classifier the
  sandbox uses for command parsing.

---
title: Ferret Security and Trademarks
description: Trademark hygiene policy and security posture for chimera ferret — sandbox, approval, redaction, eventlog, bridge auth.
---

# `chimera ferret` security and trademarks

This page covers two related topics: the trademark hygiene policy
that governs how ferret docs and source code refer to the upstream
project, and the security posture of the ferret runtime itself.

## Trademark hygiene

### Policy

We do not ship the upstream IDE-first OpenAI-flagship coding agent's
trademarks. That includes the brand name, the product name, the
project's hosted domains, and any logos, in:

- live source code (`chimera/ferret/*.py`)
- live documentation (`docs/ferret/*.md`)
- CLI text (help output, error messages, banners)
- shipped templates and prompts

### What you will see referenced

The path `~/.codex/config.toml` and its siblings (`~/.codex/agent/`,
`~/.codex/command/`, `~/.codex/AGENTS.md`, `~/.codex/log/`, etc.)
are referenced as **filesystem facts**. They name the directory
layout that ferret reads on disk so that users who already have
configuration in those locations get a working out-of-the-box
experience. They are not brand claims and do not trigger trademark
concerns.

This is the same posture otter takes for `~/.opencode/` and mink
takes for `~/.claude/` — the directory is the contract, the brand
is not.

### What you will not see referenced

Live source and live docs do **not** reproduce upstream prose, and
do **not** name the upstream brand. Where context requires, we use
neutral phrasing:

- "ferret"
- "the upstream"
- "the IDE-first OpenAI-flagship coding agent"

This is a hard rule: live files must pass a `git grep` for the
upstream brand name and hosted-domain pattern under `docs/ferret/`
and `chimera/ferret/`, with the only acceptable matches being
filesystem-fact paths (`~/.codex/...`). The grep used by CI is:

```bash
git grep -nE 'Codex CLI|codex-cli|@openai/codex' \
  docs/ferret/ chimera/ferret/
```

The grep must be empty before any merge. Filesystem references
(`~/.codex/`) do not match this pattern by design.

### Where comparative analysis lives

Internal comparative analysis (per-agent reports, design notes
naming the upstream for clarity, source-tree audits) lives under
`research/ferret/`. That directory is not shipped to users and is
not indexed by the docs site; it is fair game for naming the
upstream explicitly because its audience is internal contributors,
not end users.

When work moves from `research/ferret/` into `docs/ferret/`, it
must be rewritten to use the neutral phrasing above. A simple grep
before landing the patch is sufficient verification.

### Why this matters

Two reasons:

1. **Legal hygiene.** We don't have permission to use the upstream
   brand, and there's no need to: ferret is its own subcommand with
   its own name. Every brand reference is risk that buys us
   nothing.
2. **Identity.** Ferret is built on Chimera primitives and is not a
   reimplementation of the upstream. Calling out the parallel
   explicitly in user-facing prose blurs that distinction.

## Security posture

Ferret's security posture is the strictest of the three Chimera
coding-agent CLIs because the sandbox and approval flags are
first-class.

### Sandbox

Every ferret session runs through the sandbox guard
(`chimera/ferret/sandbox.py`). The default mode is `read-only` —
the agent cannot write anything or reach the network until you
explicitly widen.

The full mode matrix is in [`sandbox.md`](sandbox.md). The headline
guarantees:

- `read-only` — refuses every write tool and every network tool.
  Bash commands that match a write / network heuristic
  (`chimera/permissions/risk.py`) are denied.
- `workspace-write` — allows writes whose resolved absolute path is
  inside `cwd`. The check is on `realpath`, so symlink trickery
  doesn't bypass it.
- `workspace-write-network` — same filesystem rules as
  `workspace-write`; network is allowed.
- `off` — bypass; recommended only inside a disposable container.

Sandbox mode is fixed at process start. It cannot be widened
mid-session. Restart ferret to change.

### Approval

The approval preset (`chimera/ferret/approval.py`) is the
policy-layer twin of the sandbox. The full preset matrix is in
[`approval.md`](approval.md). Headline guarantees:

- `read-only` — refuses every write / network tool at the policy
  layer (in addition to the sandbox refusing them at the runtime
  layer).
- `auto` — opens the policy layer; the sandbox still constrains
  what can land.
- `full` — opens everything; suppresses even hard-deny patterns
  like `rm -rf /`. Print a banner whenever active.

A tool call must clear **both** layers — sandbox AND approval — to
execute.

### Permissions framework

Below the sandbox + approval layer, ferret reuses the standard
Chimera permission framework (`chimera/permissions/`). The default
policy:

- File reads — `allow` (with `*.env` files asking).
- File writes / edits — `allow`.
- Bash — `allow` for low-risk commands; `ask` for write / network
  commands; `deny` for the obvious destructive patterns
  (`rm -rf /`, `git push --force`, `git reset --hard`, etc.).
- LSP rename — `ask`.
- Network access from non-bash tools — gated by sandbox mode.

The session loads project- and user-level overrides from
`~/.codex/config.toml`'s `[permission]` section and any
agent-specific `permission:` frontmatter. Project rules win over
user rules win over defaults.

### Secrets

All output flows through `chimera.secrets.RedactionMiddleware`,
which redacts ten common secret patterns (API keys, AWS, Bearer
tokens, private key blocks, etc.) before printing or persisting.
This applies to:

- REPL output.
- `--output-format stream-json` events.
- ACP `session/update` notifications.
- Cloud-bridge WebSocket frames.
- Eventlog files under `~/.chimera/eventlog/ferret-<id>/`.

Secrets in tool input (e.g., a `Bearer` token in a `web_fetch`
header) are **not** redacted from the actual request — only from
the audit / display surface. Don't paste production secrets into
the prompt body.

### Cloud bridge auth

The `chimera ferret bridge` token is read **only** from the CLI
flag (`--auth-token`) or the env var (`$FERRET_BRIDGE_TOKEN`). It
is **not** read from `~/.codex/config.toml` to avoid checking
secrets into version control. See [`cloud-bridge.md`](cloud-bridge.md)
for the full contract.

The bridge refuses plain HTTP unless `--insecure` is passed (dev
only). The CLI prints a loud warning whenever the bridge connects
without TLS.

### ACP server

The ACP server (`chimera ferret serve`) speaks JSON-RPC on stdio.
Authentication is the responsibility of whatever spawned the
process (typically an editor running locally). Do not pipe the ACP
server's stdio through a public network without an authenticating
wrapper. Use `chimera ferret bridge` instead.

### HTTP server (opt-in)

The HTTP server (`chimera ferret serve --http`) is unauthenticated
by default, like otter's. The expected deployment is:

1. Run on `127.0.0.1` (the default).
2. Front it with a reverse proxy that adds auth (or an SSH tunnel
   for personal use).

Do **not** bind to `0.0.0.0` without an auth layer. The server
emits a warning at startup when the bind host is non-loopback.
`FERRET_SERVER_TOKEN`, when set, gates the HTTP surface with a
Bearer auth header — recommended for any non-loopback deployment.

### Eventlog hygiene

Sessions persist to `~/.chimera/eventlog/ferret-<utc>-<uuid>/` with
permissions inherited from the user. The directory contains:

- `summary.json` — provider, model, cost, sandbox mode, approval
  preset, file edits.
- `event-NNNNNN-<id>.json` — one file per loop event (tool call,
  tool result, agent message, sandbox violation, etc.).

Operators who want to keep these out of backups should add
`~/.chimera/eventlog/` to their backup excludes; the data is
re-creatable (it's a session log, not source-of-truth).

### Auth tokens

Provider OAuth tokens (for MCP servers, providers, share
endpoints) are cached under `~/.chimera/auth/` and
`~/.chimera/mcp-tokens/` with `0o600` permissions. The credential
store is file-based; use disk-encryption (FileVault, LUKS) for
at-rest protection.

### Audit log

Every tool call — including bridge-originated ones — is recorded
in the audit log (`chimera/permissions/audit.py`) with the active
sandbox mode, approval preset, decision, and outcome. The log is
queryable from the REPL with `/audit` and from the CLI with
`chimera ferret sessions show <id> --audit`.

### Ban-list grep

The trademark scrub is enforced by a CI grep over `docs/ferret/`
and `chimera/ferret/` for the upstream brand name and hosted-domain
pattern. Only filesystem-fact path matches (`~/.codex/...`) are
tolerated; CI fails if a non-path match appears.

The grep:

```bash
git grep -nE 'Codex CLI|codex-cli|@openai/codex' \
  docs/ferret/ chimera/ferret/
```

Must produce zero matches.

## Filing security issues

Security issues should be reported privately to the project
maintainers via the channels documented in `SECURITY.md` at the
repo root. Do not file public GitHub issues for vulnerabilities.

## See also

- [`sandbox.md`](sandbox.md) — sandbox modes.
- [`approval.md`](approval.md) — approval presets.
- [`cloud-bridge.md`](cloud-bridge.md) — bridge auth.
- [`ide.md`](ide.md) — ACP server posture.
- [`parity-matrix.md`](parity-matrix.md) — overall parity status.
- `chimera/permissions/` — permission framework.
- `chimera/secrets/` — redaction.
- [`../otter/security-and-trademarks.md`](../otter/security-and-trademarks.md) —
  sibling policy.
- [`../mink/security-and-licenses.md`](../mink/security-and-licenses.md) —
  sibling policy.

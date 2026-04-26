---
title: Otter Security and Trademarks
description: Otter's threat model, secret handling, network/auth posture, and the trademark scrub policy enforced by CI.
---

# `chimera otter` security and trademarks

This page covers two related topics: the trademark hygiene policy
that governs how otter docs and source code refer to the upstream
project, and the security posture of the otter runtime itself.

## Trademark hygiene

### Policy

We do not ship the upstream open-source coding agent's trademarks.
That includes the brand name, the product name, the project's hosted
domains, and any logos, in:

- live source code (`chimera/otter/*.py`)
- live documentation (`docs/otter/*.md`)
- CLI text (help output, error messages, banners)
- shipped templates and prompts

### What you will see referenced

The path `~/.opencode/config.json` and its siblings (`.opencode/`
in a project tree, `~/.opencode/plugin/<name>/`,
`~/.opencode/AGENTS.md`, etc.) are referenced as **filesystem facts**.
They name the directory layout that otter reads on disk so that users
who already have configuration in those locations get a working
out-of-the-box experience. They are not brand claims and do not
trigger trademark concerns.

In the same spirit we accept `.cursor/rules/*.mdc` files (a convention
introduced by another editor); the path is a fact about where the
file lives, not an endorsement.

### What you will not see referenced

Live source and live docs do **not** reproduce upstream prose, and
do **not** name the upstream brand. Where context requires, we use
neutral phrasing:

- "the open-source coding agent"
- "the upstream"
- "the upstream coding-agent ecosystem"

This is a hard rule: live files must pass a `git grep` for the
upstream brand name and hosted-domain pattern under `docs/otter/`
and `chimera/otter/`, with the only acceptable matches being
filesystem-fact paths (`~/.opencode/...`, `.opencode/...`). The exact
grep pattern is captured in `chimera/otter/trademark.py`.

### Where comparative analysis lives

Internal comparative analysis (per-agent reports, design notes
naming the upstream for clarity, source-tree audits) lives under
`research/otter/`. That directory is not shipped to users and is
not indexed by the docs site; it is fair game for naming the
upstream explicitly because its audience is internal contributors,
not end users.

When work moves from `research/otter/` into `docs/otter/`, it must be
rewritten to use the neutral phrasing above. A simple grep before
landing the patch is sufficient verification.

### Why this matters

Two reasons:

1. **Legal hygiene.** We don't have permission to use the upstream
   brand, and there's no need to: otter is its own subcommand with
   its own name. Every brand reference is risk that buys us nothing.
2. **Identity.** Otter is built on Chimera primitives and is not a
   reimplementation of the upstream. Calling out the parallel
   explicitly in user-facing prose blurs that distinction.

## Security posture

### Permissions

Every otter session runs through the same permission framework as
mink (`chimera/permissions/`). The default policy is:

- File reads — `allow` (with `*.env` files asking).
- File writes/edits — `allow`.
- Bash — `allow` for low-risk commands; `ask` for write/network
  commands (heuristic in `chimera/permissions/risk.py`); `deny` for
  the obvious destructive patterns (`rm -rf /`, `git push --force`,
  `git reset --hard`, etc.).
- LSP rename — `ask`.
- Network access from non-bash tools — `allow` (web fetch / search
  are intentional read tools).

The session loads project- and user-level overrides from
`AGENTS.md`, `.opencode/config.json` (`permission` key), and any
agent-specific `permission:` frontmatter. Project rules win over user
rules win over defaults.

### Secrets

All output flows through `chimera.secrets.RedactionMiddleware`,
which redacts ten common secret patterns (API keys, AWS, Bearer
tokens, private key blocks, etc.) before printing or persisting.
This applies to:

- REPL output.
- `--output-format stream-json` events.
- Eventlog files under `~/.chimera/eventlog/otter-<id>/`.
- Share transcripts emitted by `chimera otter share`.

Secrets in tool input (e.g., a `Bearer` token in a `web_fetch`
header) are **not** redacted from the actual request — only from the
audit / display surface. Don't paste production secrets into the
prompt body.

### Sandbox

Otter ships a `--sandbox` flag (wave-2) that wraps tool execution in
an OS-level sandbox (macOS sandbox-exec, Linux bubblewrap when
available). Wave-1 operators rely on permission rules + cwd scoping
instead. The recommendation for high-stakes work is to run otter in a
disposable container (`chimera otter --cwd /work` from inside a
Docker container).

### Server (HTTP)

The HTTP server (`chimera otter serve`) is unauthenticated by
default. The expected deployment is:

1. Run on `127.0.0.1` (the default).
2. Front it with a reverse proxy that adds auth (or an SSH tunnel
   for personal use).

Do **not** bind to `0.0.0.0` without an auth layer. The server emits
a warning at startup when the bind host is non-loopback.

### ACP server

The ACP server (`chimera otter serve --acp`) speaks JSON-RPC on
stdio. Authentication is the responsibility of whatever spawned the
process (typically an IDE running locally). Do not pipe the ACP
server's stdio through a public network without an authenticating
wrapper.

### Eventlog hygiene

Sessions persist to `~/.chimera/eventlog/otter-<utc>-<uuid>/` with
permissions inherited from the user. The directory contains:

- `summary.json` — provider, model, cost, file edits.
- `event-NNNNNN-<id>.json` — one file per loop event (tool call,
  tool result, agent message, etc.).

Operators who want to keep these out of backups should add
`~/.chimera/eventlog/` to their backup excludes; the data is
re-creatable (it's a session log, not source-of-truth).

### Auth tokens

OAuth tokens (for MCP servers, providers, share endpoints) are
cached under `~/.chimera/auth/` and `~/.chimera/mcp-tokens/` with
`0o600` permissions. The credential store is file-based; use
disk-encryption (FileVault, LUKS) for at-rest protection.

### Ban-list grep

The trademark scrub is enforced by `chimera/otter/trademark.py` (a
dev-only helper) and a CI grep over `docs/otter/` and
`chimera/otter/` for the brand name and the hosted-domain pattern.
Only filesystem-fact path matches (`~/.opencode/...`,
`.opencode/...`) are tolerated; CI fails if a non-path match appears.

## Filing security issues

Security issues should be reported privately to the project
maintainers via the channels documented in `SECURITY.md` at the repo
root. Do not file public GitHub issues for vulnerabilities.

## See also

- `chimera/otter/trademark.py` — dev-only scrub helper.
- `chimera/permissions/` — permission framework.
- `chimera/secrets/` — redaction.
- `docs/mink/security-and-licenses.md` — the mink twin (similar
  policy, different baseline).
- `docs/otter/parity-matrix.md` — overall parity status.

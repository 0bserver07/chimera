---
title: Weasel security and trademarks
description: Trademark hygiene policy for chimera weasel, plus the security posture of the runtime — permissions, secrets, sandboxing, RPC transport, and credential storage.
---

# `chimera weasel` security and trademarks

This page covers two related topics: the trademark hygiene policy
that governs how weasel docs and source code refer to the upstream
project, and the security posture of the weasel runtime itself.

## Trademark hygiene

### Policy

We do not ship the upstream minimal harness's trademarks. That
includes the brand name, the product name, the project's hosted
domains, and any logos, in:

- live source code (`chimera/weasel/*.py`)
- live documentation (`docs/weasel/*.md`)
- CLI text (help output, error messages, banners)
- shipped templates and prompts

### What you will see referenced

Filesystem-fact paths are OK. Where weasel needs to mention a
directory layout that another tool created on disk — for migration
guidance, or because a settings file is genuinely on the user's
system — the path itself is referenced as a fact, not as a brand
endorsement. Examples that are acceptable in live docs:

- `.weasel/extensions/` — weasel's own.
- `~/.weasel/extensions/` — weasel's own.
- `.weasel/settings.json` — weasel's own.

For the upstream minimal harness, weasel **does not** silently
ingest from upstream paths. If a doc needs to help a user migrate
its config, it can name the path as the source they're moving from
— but the runtime does not auto-read those locations.

### What you will not see referenced

Live source and live docs do **not** reproduce upstream prose, do
**not** name the upstream brand, do **not** name the upstream
package on npm, and do **not** name the upstream's hosted domains.
Where context requires referring to the upstream, we use neutral
phrasing:

- "the minimal harness"
- "the upstream"
- "the upstream coding-agent harness"

This is a hard rule: live files must pass a `git grep` for the
upstream brand name and any of its hosted-domain patterns under
`docs/weasel/` and `chimera/weasel/`. CI fails the build if a match
appears.

### Where comparative analysis lives

Internal comparative analysis (per-agent reports, design notes
naming the upstream for clarity, source-tree audits) lives under
`research/weasel/`. That directory is not shipped to users and is
not indexed by the docs site; it is fair game for naming the
upstream explicitly because its audience is internal contributors,
not end users.

When work moves from `research/weasel/` into `docs/weasel/`, it
must be rewritten to use the neutral phrasing above. A simple grep
before landing the patch is sufficient verification.

### Why this matters

Two reasons:

1. **Legal hygiene.** We don't have permission to use the upstream
   brand, and there's no need to: weasel is its own subcommand with
   its own name. Every brand reference is risk that buys us nothing.
2. **Identity.** Weasel is built on Chimera primitives and is not a
   reimplementation of the upstream. Calling out the parallel
   explicitly in user-facing prose blurs that distinction.

## Security posture

### Permissions

Every weasel session runs through the same permission framework as
mink, otter, and ferret (`chimera/permissions/`). The default
policy is:

- File reads — `allow` (with `*.env` files asking).
- File writes / edits — `allow`.
- Bash — `allow` for low-risk commands; `ask` for write / network
  commands (heuristic in `chimera/permissions/risk.py`); `deny` for
  the obvious destructive patterns (`rm -rf /`, `git push --force`,
  `git reset --hard`, etc.).
- LSP rename — `ask`.
- Network access from non-bash tools — `allow` (web fetch / search
  are intentional read tools).

The session loads project- and user-level overrides from
`.weasel/settings.json` (`permissions` key) and any extension's
manifest `permissions` block. Extension manifests can only **tighten**
the user's policy, never relax it. Project rules win over user
rules win over defaults.

### Extensions and trust

Auto-discovered extensions are powerful — they can register tools
that run arbitrary code, hook into pre-tool events, and read /
write the project. Weasel treats new extensions as untrusted on
first encounter:

- **Interactive mode.** First load of a new extension prompts for
  approval. The user's choice is recorded in
  `.weasel/settings.json` under `extensions.allowed`.
- **Print / RPC / SDK modes.** Unattended runs require an explicit
  `--allow-extensions <names>` flag (or `Agent(allow_extensions=...)`
  in the SDK), or the extension is silently skipped with a stderr
  warning.
- **Per-extension permissions.** A manifest `permissions` block
  can declare `bash: ask` or `write: deny` to refuse a tool the
  extension itself does not need; this only tightens.

To audit what is loaded:

```bash
chimera weasel /extensions      # in REPL
chimera weasel --print-extensions
```

### Secrets

All output flows through `chimera.secrets.RedactionMiddleware`,
which redacts ten common secret patterns (API keys, AWS, Bearer
tokens, private key blocks, etc.) before printing or persisting.
This applies to:

- REPL output.
- `--stream-json` events.
- RPC `event` notifications.
- Eventlog files under `~/.chimera/eventlog/weasel-<id>/`.

Secrets in tool input (e.g., a `Bearer` token in a `web_fetch`
header) are **not** redacted from the actual outbound request —
only from the audit / display surface. Don't paste production
secrets into the prompt body.

### Sandbox

Weasel does **not** ship its own OS-level sandbox. The deliberate
trade-off is to stay minimal: ferret is the Chimera CLI that owns
the sandbox-flag surface; users who need sandboxed execution
should reach for `chimera ferret --sandbox <mode>` instead.

For weasel, the recommended high-stakes posture is:

1. Run inside a disposable container (`chimera weasel --cwd /work`
   from inside a Docker container).
2. Or restrict the toolbelt: `--allowed-tools Read` produces a
   read-only agent that can't shell out at all.
3. Or use a tightening permission policy: `Agent(permissions=
   AlwaysDeny(scope=["bash"]))` in SDK mode.

### RPC mode transport

`chimera weasel --mode rpc` speaks JSON-RPC on stdio.
Authentication is the responsibility of whatever spawned the
process. Do **not** pipe the RPC server's stdio through a public
network without an authenticating wrapper — there is no built-in
auth on the transport itself, by design (it is meant to be a
local subprocess).

If you need a network-reachable weasel, run weasel inside an SSH
session or an authenticated reverse proxy that owns auth.

### Eventlog hygiene

Sessions persist to `~/.chimera/eventlog/weasel-<utc>-<uuid>/` with
permissions inherited from the user. The directory contains:

- `summary.json` — provider, model, cost, file edits.
- `event-NNNNNN-<id>.json` — one file per loop event (tool call,
  tool result, agent message, etc.).

Operators who want to keep these out of backups should add
`~/.chimera/eventlog/` to their backup excludes; the data is
re-creatable (it's a session log, not source-of-truth).

To purge: `rm -rf ~/.chimera/eventlog/weasel-*`.

### Auth tokens

OAuth tokens (for MCP servers, providers, share endpoints) are
cached under `~/.chimera/credentials.json` and `~/.chimera/auth/`
with `0o600` permissions. The credential store is file-based; use
disk-encryption (FileVault, LUKS) for at-rest protection.

`CredentialStore._write` chmods to `0o600` after each save
(`chimera/auth/store.py`).

### `.weasel/settings.json` integrity

Weasel writes to `.weasel/settings.json` only on user opt-in
events: extension allow / block decisions, model preferences set
via `/model`, the cycle list pinned by `--models`. Each write
preserves any unknown keys it encounters — out-of-tree tools that
share the same file (a hypothetical extension installer, for
instance) keep their data intact across weasel sessions.

The file is a plaintext JSON document. Do **not** put secrets in
it; secrets belong in env vars or the credential store.

### Ban-list grep

The trademark scrub is enforced by a CI grep over `docs/weasel/`
and `chimera/weasel/` for the upstream brand name and any of its
hosted-domain patterns. Only weasel-owned filesystem-fact paths
(`.weasel/...`, `~/.weasel/...`) are tolerated; CI fails if any
match for the banned set appears.

The exact grep patterns live in `chimera/weasel/trademark.py` (a
dev-only helper).

## Filing security issues

Security issues should be reported privately to the project
maintainers via the channels documented in `SECURITY.md` at the
repo root. Do not file public GitHub issues for vulnerabilities.

## See also

- `chimera/weasel/trademark.py` — dev-only scrub helper.
- `chimera/permissions/` — permission framework.
- `chimera/secrets/` — redaction.
- [`extensions.md`](extensions.md) — first-load trust prompts and
  the manifest `permissions` block.
- [`modes.md`](modes.md) — RPC transport details.
- [`parity-matrix.md`](parity-matrix.md) — overall parity status.
- [`docs/otter/security-and-trademarks.md`](../otter/security-and-trademarks.md) — sibling policy with the same posture.
- [`docs/ferret/sandbox.md`](../ferret/sandbox.md) — when you need
  an OS-level sandbox, ferret is the Chimera CLI that owns it.

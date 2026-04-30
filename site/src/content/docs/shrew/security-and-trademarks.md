---
title: Shrew security and trademarks
description: Trademark hygiene policy for chimera shrew, plus the security posture of the runtime — local providers, secrets, sandboxing, eventlog hygiene, and the scrub script.
---

# `chimera shrew` security and trademarks

This page covers two related topics: the trademark hygiene policy
that governs how shrew docs and source code refer to the upstream
project, and the security posture of the shrew runtime itself.

## Trademark hygiene

### Policy

We do not ship the upstream small-model coding agent's trademarks.
That includes the brand name, the product name, the project author's
GitHub handle, and any logos, in:

- live source code (`chimera/shrew/*.py`)
- live documentation (`docs/shrew/*.md`)
- live tests (`tests/shrew/*`)
- CLI text (help output, error messages, banners)
- shipped templates and prompts

### What you will see referenced

Filesystem-fact paths are OK. Where shrew needs to mention a
directory layout that exists on disk — for migration guidance, or
because a settings file is genuinely on the user's system — the
path itself is referenced as a fact, not as a brand endorsement.
Examples that are acceptable in live docs:

- `~/.shrew/skills/` — shrew's own user overlay.
- `.shrew/skills/` — shrew's own project overlay.
- `~/.chimera/eventlog/shrew-<id>/` — chimera's eventlog directory.
- `~/.chimera/datasets/aider-polyglot/` — chimera's dataset cache.
- `qwen3.6-35b-a3b` — public Qwen MoE checkpoint id.

For the upstream small-model coding agent, shrew **does not**
silently ingest from upstream paths. If a doc needs to help a user
migrate config, it can name the path as the source they're moving
from — but the runtime does not auto-read those locations.

### What you will not see referenced

Live source and live docs do **not** reproduce upstream prose, do
**not** name the upstream brand, do **not** name the upstream
package on npm or PyPI, do **not** name the upstream's hosted
domains, and do **not** name the upstream author's GitHub handle.
Where context requires referring to the upstream, we use neutral
phrasing:

- "shrew"
- "the upstream"
- "the small-model coding agent"
- "the upstream coding-agent project"

This is a hard rule: live files must pass a `git grep` for the
upstream brand name and the project author's handle under
`docs/shrew/`, `chimera/shrew/`, and `tests/shrew/`. CI fails the
build if a match appears.

### Where comparative analysis lives

Internal comparative analysis (per-agent reports, design notes
naming the upstream for clarity, source-tree audits) lives under
`research/shrew/`. That directory is not shipped to users and is
not indexed by the docs site; it is fair game for naming the
upstream explicitly because its audience is internal contributors,
not end users.

The canonical SPEC (`research/shrew/SPEC.md`) is the one
exception that **is** scrubbed by CI — it sits at the boundary
between research and live source, and we hold it to the same
neutrality bar as everything below `chimera/shrew/`.

When work moves from `research/shrew/` into `docs/shrew/`, it
must be rewritten to use the neutral phrasing above. A simple
local run of `bash scripts/shrew_trademark_scrub.sh` is sufficient
verification before landing the patch.

### The scrub script

The hygiene check is enforced by
[`scripts/shrew_trademark_scrub.sh`](https://github.com/0bserver07/chimera/blob/master/scripts/shrew_trademark_scrub.sh).
It greps the live paths above for the banned set and exits `1` on
any hit outside the explicit allow-list. It runs in CI under the
`shrew-trademark-scrub` job and locally with no flags:

```bash
bash scripts/shrew_trademark_scrub.sh
# shrew trademark scrub: OK (no branded mentions in live source/docs)
```

The script is intentionally conservative — it greps for the
banned strings literally, with a small allow-list for shrew-owned
filesystem-fact paths. False positives (rare) can be addressed by
rephrasing; do not loosen the regex.

### Why this matters

Two reasons:

1. **Legal hygiene.** We don't have permission to use the upstream
   brand, and there's no need to: shrew is its own subcommand with
   its own name. Every brand reference is risk that buys us
   nothing.
2. **Identity.** Shrew is built on Chimera primitives and weasel
   substrate; it is **not** a reimplementation of the upstream.
   Calling out the parallel explicitly in user-facing prose blurs
   that distinction. Shrew is its own thing.

## Security posture

### Local providers

Shrew is small-model-first, which means most users run it against
a local llama.cpp or Ollama server bound to `127.0.0.1`. The
runtime does not auto-bind to non-loopback interfaces. If you
manually point shrew at a remote llama.cpp / Ollama via
`$LLAMACPP_BASE_URL` / `$OLLAMA_BASE_URL`, you are responsible
for the transport security of that link (TLS reverse proxy, SSH
tunnel, VPN — pick one).

The probe path uses `urllib.request` with a 250ms socket timeout,
stdlib-only. It does not send credentials in the probe.

### Permissions

Every shrew session runs through the same permission framework as
the rest of Chimera (`chimera/permissions/`). The default policy
is identical to weasel's:

- File reads — `allow` (with `*.env` files asking).
- File writes / edits — `allow`.
- Bash — `allow` for low-risk commands; `ask` for write / network
  commands; `deny` for the obvious destructive patterns
  (`rm -rf /`, `git push --force`, `git reset --hard`).
- LSP rename — `ask`.
- Network access from non-bash tools — `allow` (web fetch / search
  are intentional read tools).

Shrew's restrictive `--allowed-tools=Read,Write,Edit,Bash` default
narrows the surface further. To opt back into the full default
tool group: `--allowed-tools=`.

### Sandbox

Shrew does **not** ship its own OS-level sandbox. The deliberate
trade-off is to stay minimal: ferret is the Chimera CLI that owns
the sandbox-flag surface; users who need sandboxed execution
should reach for `chimera ferret --sandbox <mode>` instead.

For shrew, the recommended high-stakes posture is:

1. Run inside a disposable container (`chimera shrew --cwd /work`
   from inside a Docker container).
2. Or restrict the toolbelt: `--allowed-tools Read` produces a
   read-only agent that can't shell out.
3. Or use a tightening permission policy in SDK mode.

### Secrets

All output flows through `chimera.secrets.RedactionMiddleware`,
which redacts ten common secret patterns (API keys, AWS, Bearer
tokens, private key blocks, etc.) before printing or persisting.
This applies to:

- REPL output.
- `--json` blobs.
- Eventlog files under `~/.chimera/eventlog/shrew-<id>/`.

Secrets in tool input (e.g., a `Bearer` token in a `web_fetch`
header) are **not** redacted from the actual outbound request —
only from the audit / display surface. Don't paste production
secrets into the prompt body.

### RPC mode transport

`chimera shrew --mode rpc` late-binds to weasel's RPC server,
which speaks JSON-RPC on stdio. Authentication is the
responsibility of whatever spawned the process. Do **not** pipe
the RPC server's stdio through a public network without an
authenticating wrapper — there is no built-in auth on the
transport itself, by design (it is meant to be a local
subprocess).

### Eventlog hygiene

Sessions persist to `~/.chimera/eventlog/shrew-<utc>-<uuid>/` with
permissions inherited from the user. The directory contains:

- `summary.json` — provider, model, cost, file edits.
- `event-NNNNNN-<id>.json` — one file per loop event.

Operators who want to keep these out of backups should add
`~/.chimera/eventlog/` to their backup excludes; the data is
re-creatable.

To purge: `rm -rf ~/.chimera/eventlog/shrew-*`.

### Auth tokens

OAuth tokens (for MCP servers, providers, share endpoints) are
cached under `~/.chimera/credentials.json` with `0o600`
permissions. The credential store is file-based; use disk
encryption (FileVault, LUKS) for at-rest protection. The
implementation is in `chimera/auth/store.py` and is shared across
all chimera CLIs.

### Llama.cpp / Ollama API keys

Some local servers gate on a key (`llama-server --api-key foo`).
Shrew picks up:

- `$LLAMACPP_API_KEY` — sent as the `Authorization: Bearer ...`
  header to llama.cpp.
- `$OLLAMA_API_KEY` — sent the same way to Ollama's OpenAI shim.

Both default to `sk-noauth` when unset, which most local setups
accept. Do not hard-code real keys in the env; use a secret
manager and inject at session start.

### Datasets

Shrew **does not** vendor third-party datasets (Aider Polyglot,
GAIA). Users stage them locally under `~/.chimera/datasets/` and
the harness reads from there. This sidesteps:

- License issues with redistributing the corpora.
- Surprise downloads from CI environments.
- Dataset version drift between contributors.

The trade-off is each user / CI setup stages the dataset once.
Setup hints in `chimera shrew bench <name>` make this explicit
when the dataset is missing (exit code `3`).

## Filing security issues

Security issues should be reported privately to the project
maintainers via the channels documented in `SECURITY.md` at the
repo root. Do not file public GitHub issues for vulnerabilities.

## See also

- `scripts/shrew_trademark_scrub.sh` — the CI scrub script.
- `chimera/permissions/` — permission framework.
- `chimera/secrets/` — redaction.
- [`quickstart.md`](quickstart.md) — first-run walkthrough.
- [`small-model-setup.md`](small-model-setup.md) — local server
  bind addresses and transport.
- [`parity-matrix.md`](parity-matrix.md) — overall parity status
  including which trademark-sensitive surfaces are red-lined.
- [`docs/weasel/security-and-trademarks.md`](../weasel/security-and-trademarks.md) —
  sibling policy with the same posture.
- [`docs/ferret/sandbox.md`](../ferret/sandbox.md) — when you need
  an OS-level sandbox, ferret is the Chimera CLI that owns it.

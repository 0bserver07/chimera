---
title: Ferret Quickstart
description: Install ferret, run a sandboxed first turn, choose an approval preset, and bring up the IDE-first ACP server.
---

# `chimera ferret` Quickstart

`chimera ferret` is the third Chimera coding-agent CLI. Where
[`chimera mink`](../mink/quickstart.md) mirrors a TUI-first agent and
[`chimera otter`](../otter/quickstart.md) mirrors a server-first
multi-client agent, ferret mirrors the IDE-first OpenAI-flagship
coding agent: a sandbox-first runner with single-flag approval
presets, an ACP JSON-RPC transport that ships as the default `serve`
transport, and an optional cloud bridge so a local ferret session
can be driven from a remote UI.

This page walks the four entry points end-to-end. For deeper dives:

- [`providers.md`](providers.md) — OpenAI-flagship provider chain.
- [`sandbox.md`](sandbox.md) — three sandbox modes and what each blocks.
- [`approval.md`](approval.md) — read-only / auto / full approval presets.
- [`ide.md`](ide.md) — IDE-first ACP schema and notification kinds.
- [`cloud-bridge.md`](cloud-bridge.md) — `chimera ferret bridge` setup.
- [`parity-matrix.md`](parity-matrix.md) — surface-by-surface parity status.
- [`security-and-trademarks.md`](security-and-trademarks.md) — trademark + security policy.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- One of: an OpenAI API key, an Anthropic API key, an OpenRouter API
  key, or a running Ollama daemon

```bash
uv --version                         # >= 0.4
uv sync --extra dev --extra openai   # core + OpenAI SDK
```

The OpenAI extra is recommended because the ferret default model is
`gpt-5` (falling back to `gpt-4o` when the GPT-5 family is not yet
available on your account). If you'd rather drive ferret through
Anthropic, OpenRouter, or Ollama, see [`providers.md`](providers.md).

## Provider configuration

Ferret resolves the provider in this order (first match wins):

1. `--model <id>` on the CLI.
2. `$FERRET_MODEL` environment variable.
3. `$OPENAI_API_KEY` set → defaults to `gpt-5` (falls back to `gpt-4o`).
4. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
5. `$OPENROUTER_API_KEY` set → defaults to `openai/gpt-5`.
6. Friendly error pointing at the env vars above.

Set whichever key you have:

```bash
export OPENAI_API_KEY=sk-...
# OR
export ANTHROPIC_API_KEY=sk-ant-...
# OR
export OPENROUTER_API_KEY=sk-or-...
```

Or, for a local model:

```bash
ollama serve &
ollama pull qwen3:32b
chimera ferret --model qwen3:32b -p "explain this repo"
```

## First one-shot turn

The simplest entry point: `-p` runs a single turn and exits. Ferret
defaults to the **read-only** sandbox mode, so the first run is safe
to point at any directory:

```bash
chimera ferret -p "list the top-level files and read the README"
```

Expected output shape:

```text
[ferret] sandbox=read-only approval=read-only model=gpt-5

I'll list the repo first, then read the README.

▶ list_files(path=".")
CHANGELOG.md  CLAUDE.md  README.md  chimera/  docs/  examples/  tests/

▶ Read(path="README.md")
# Chimera
A composable coding agent framework
...

The repo root has a README pitching Chimera as a composable coding agent framework.

[ferret] run saved as ferret-20260430T101455-1f3c2a8b at /Users/.../.chimera/eventlog/ferret-20260430T101455-1f3c2a8b/
```

Streaming text appears as it arrives. Tool calls render as
`▶ <Tool>(<args>)` lines. The `[ferret] sandbox=... approval=...`
banner on the first line tells you exactly which guardrails are
active. The trailing `run saved as ...` line on stderr points at the
persisted run directory.

## Sandbox + approval flags

Ferret ships two single-flag guardrails. They are the headline
differentiator versus mink/otter: pick a sandbox mode and pick an
approval preset, no fine-grained tool allowlists required.

```bash
# Default — safest possible. Only reads.
chimera ferret -p "audit the repo"

# Workspace-write — writes inside cwd, no network.
chimera ferret --sandbox workspace-write \
               --approval auto \
               -p "fix the failing test"

# Workspace-write + network — writes inside cwd, allows network.
chimera ferret --sandbox workspace-write-network \
               --approval auto \
               -p "pip install foo, run it, then check the diff"

# Full power — every tool allowed, no asks. Use with care.
chimera ferret --sandbox workspace-write-network \
               --approval full \
               -p "ship it"
```

Sandbox modes are documented in detail in [`sandbox.md`](sandbox.md).
Approval presets are documented in [`approval.md`](approval.md). The
two flags compose: the sandbox blocks at the OS level, the approval
preset blocks at the policy level. A tool call has to pass both.

You can change the approval preset mid-session in the REPL with
`/approval auto` (see [`approval.md`](approval.md)). The sandbox
mode is fixed at process start because it controls OS-level kernel
hooks; restart ferret with a different `--sandbox` flag to widen.

## Drop into the REPL

Run `chimera ferret` with no `-p` flag for an interactive REPL:

```bash
chimera ferret
chimera ferret --sandbox workspace-write --approval auto
```

The REPL streams assistant text + tool calls inline, accepts mid-turn
steering, supports `Ctrl-C` cancellation, and exposes the standard
Chimera slash-command palette plus three ferret-specific entries:
`/sandbox` (show current mode), `/approval` (show or change the
preset), and `/bridge` (status of any active cloud bridge). Type
`/help` for the full list.

## Bring up the IDE bridge (ACP server)

Ferret's `serve` defaults to **ACP over stdio**, not HTTP. This is
the IDE-first stance: most ferret users drive ferret from an IDE
plugin (Zed, VS Code) that already speaks ACP.

```bash
# ACP over stdio (the default)
chimera ferret serve

# HTTP server, opt-in
chimera ferret serve --http --port 5173
```

The IDE-first ACP schema, notification kinds, and a worked Zed +
VS Code recipe live in [`ide.md`](ide.md). The HTTP server surface
mirrors `chimera otter serve` and is documented in
[`../otter/server.md`](../otter/server.md).

## Optional: cloud bridge

When you want a remote UI (a teammate's web dashboard, a phone, an
on-call alerting console) to drive a local ferret session, you can
forward the ACP transport over an authenticated HTTPS bridge:

```bash
chimera ferret bridge --remote-url https://ferret.example.com \
                      --auth-token "$FERRET_BRIDGE_TOKEN"
```

The remote-URL contract, auth-token format, and reconnection
semantics live in [`cloud-bridge.md`](cloud-bridge.md). The bridge
is optional — local ACP / HTTP servers cover the standard cases.

## Ferret config file

Ferret ingests `~/.codex/config.toml` as a filesystem fact: when the
file exists, ferret will pick up the `model`, `provider`, `sandbox`,
`approval`, and `mcp` sections that map onto Chimera primitives. CLI
flags always win over the config file, which always wins over env
vars. See [`parity-matrix.md`](parity-matrix.md) for the full key map.

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `FERRET_MODEL` | (unset) | Default model id when `--model` is not passed. See [`providers.md`](providers.md). |
| `OPENAI_API_KEY` | (unset) | Activates the OpenAI provider chain (default for ferret). |
| `ANTHROPIC_API_KEY` | (unset) | Activates the Anthropic provider chain. |
| `OPENROUTER_API_KEY` | (unset) | Activates the OpenRouter (compatible) provider chain. |
| `FERRET_BRIDGE_TOKEN` | (unset) | Bearer auth token for `chimera ferret bridge`. |
| `FERRET_SANDBOX` | (unset) | Default sandbox mode (`read-only`, `workspace-write`, `workspace-write-network`). |
| `FERRET_APPROVAL` | (unset) | Default approval preset (`read-only`, `auto`, `full`). |
| `NO_COLOR` | (unset) | When set, force the plain output handler. |

## Where to go next

- New to provider selection? Start with [`providers.md`](providers.md).
- About to grant write access? Read [`sandbox.md`](sandbox.md) and
  [`approval.md`](approval.md) together — they compose.
- Wiring an IDE plugin? See [`ide.md`](ide.md).
- Driving from a remote UI? See [`cloud-bridge.md`](cloud-bridge.md).
- Want the surface-by-surface parity status? See
  [`parity-matrix.md`](parity-matrix.md).

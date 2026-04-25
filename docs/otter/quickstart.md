---
title: Otter Quickstart
description: Install otter, run your first one-shot turn, drop into the REPL, and bring up the HTTP server in five minutes.
---

# `chimera otter` Quickstart

`chimera otter` is the second Chimera coding-agent CLI. Where [`chimera mink`](../mink/quickstart.md) mirrors a TUI-first agent, otter mirrors a server-first / multi-client open-source agent: a single ReAct loop that you can drive from a one-shot CLI, an interactive REPL, an HTTP server with SSE streaming, or an ACP JSON-RPC transport — backed by the same `LoopConfig`, tool registry, and event-sourced session store the rest of Chimera uses.

This page walks the four entry points end-to-end. For deeper dives, see:

- [`providers.md`](providers.md) — supported provider chain (Anthropic, OpenAI, OpenRouter, Ollama, Modal, custom).
- [`models.md`](models.md) — model id catalogue + the `OTTER_MODEL` env var.
- [`sessions.md`](sessions.md) — eventlog layout under `~/.chimera/eventlog/otter-*/`.
- [`share.md`](share.md) — share sinks (file / http / stdout) and the share format.
- [`server.md`](server.md) — `chimera otter serve` HTTP API + SSE event format.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- One of: an Anthropic API key, an OpenAI API key, an OpenRouter API key, or a running Ollama daemon

```bash
uv --version                          # >= 0.4
uv sync --extra dev --extra anthropic # core + Anthropic SDK
```

The Anthropic extra is recommended because the otter default model is `claude-sonnet-4-6`. If you'd rather drive otter through OpenAI, OpenRouter, or Ollama, swap the extra (`--extra openai`) or skip it entirely (the OpenAI-compatible adapter is stdlib + httpx).

## Provider configuration

Otter resolves the provider in this order (first match wins):

1. `--model <id>` on the CLI.
2. `$OTTER_MODEL` environment variable.
3. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
4. `$OPENROUTER_API_KEY` set → defaults to `anthropic/claude-sonnet-4`.
5. `$OPENAI_API_KEY` set → defaults to `gpt-4o`.
6. Friendly error pointing at the three env vars above.

Set whichever key you have:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# OR
export OPENAI_API_KEY=sk-...
# OR
export OPENROUTER_API_KEY=sk-or-...
```

Or, for a local model:

```bash
ollama serve &
ollama pull qwen3:32b
chimera otter --model qwen3:32b -p "explain this repo"
```

The full matrix lives in [`providers.md`](providers.md).

## First one-shot turn

The simplest entry point: `-p` runs a single turn and exits.

```bash
chimera otter -p "list the top-level files and read the README"
```

Expected output shape:

```text
I'll list the repo first, then read the README.

▶ list_files(path=".")
CHANGELOG.md  CLAUDE.md  README.md  chimera/  docs/  examples/  tests/

▶ Read(path="README.md")
# Chimera
A composable coding agent framework
...

The repo root has a README pitching Chimera as a composable coding agent framework.

[otter] run saved as otter-20260425T091201-71032a5e at /Users/.../.chimera/eventlog/otter-20260425T091201-71032a5e/
```

Streaming text appears as it arrives. Tool calls render as `▶ <Tool>(<args>)` lines. The trailing `[otter] run saved as ...` line on stderr points at the persisted run directory under `~/.chimera/eventlog/`. See [`sessions.md`](sessions.md) for what's in that directory and how to inspect it.

Useful one-shot flags:

```bash
chimera otter --model gpt-4o -p "draft a release note"
chimera otter --output-format json -p "ship it"          # single result blob
chimera otter --output-format stream-json -p "ship it"   # one JSON line per event
chimera otter --max-steps 10 -p "summarize"
chimera otter --allowed-tools Read,Bash -p "audit"       # tool allowlist
chimera otter --no-save -p "ad-hoc, don't journal"       # skip eventlog
chimera otter --no-color -p "..." | tee otter.log        # plain text for pipes
```

## Drop into the REPL

Run `chimera otter` with no `-p` flag for an interactive REPL:

```bash
chimera otter
```

The REPL streams assistant text + tool calls inline, accepts mid-turn steering, supports `Ctrl-C` cancellation, and exposes a slash-command palette of 26 entries — `/help`, `/exit`, `/share`, `/agent`, `/agents`, `/model`, `/models`, `/init`, `/sessions`, `/new`, `/clear`, `/cost`, `/tools`, `/yolo`, `/themes`, `/status`, `/mcp`, `/connect`, `/edit`, `/undo`, `/redo`, `/doctor`, `/config`, `/compact`, `/session`. Type `/help` at any prompt to see the live list.

Each REPL session is event-sourced under `~/.chimera/eventlog/otter-<utc>-<uuid>/`. See [`sessions.md`](sessions.md) for the on-disk layout and how to resume.

## Bring up the HTTP server

For multi-client use (a separate TUI, an IDE plugin, an evals harness), run otter as a headless HTTP server with REST endpoints + SSE streaming:

```bash
chimera otter serve --port 5173
```

The server exposes `/sessions`, `/sessions/{id}`, `/sessions/{id}/turns`, `/sessions/{id}/events` (SSE), and a few control endpoints. The full API surface, the SSE event format, and the optional `OTTER_SERVER_TOKEN` Bearer-auth are documented in [`server.md`](server.md).

For an ACP (Agent Client Protocol) JSON-RPC server over stdio (consumed by IDE clients that already speak ACP), use:

```bash
chimera otter serve --acp
```

## Inspecting and sharing sessions

Every persisted run is listable and showable:

```bash
chimera otter sessions list
chimera otter sessions show otter-20260425T091201-71032a5e
chimera otter sessions list --since 7d --json
```

To package a session into a portable transcript (HTML, Markdown, or JSON) and route it to a file, an HTTP collector, or stdout:

```bash
chimera otter share otter-20260425T091201-71032a5e
chimera otter share <id> --sink http --format json
chimera otter share <id> --sink stdout --format md | less
```

Details live in [`sessions.md`](sessions.md) and [`share.md`](share.md).

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `OTTER_MODEL` | (unset) | Default model id when `--model` is not passed. See [`models.md`](models.md). |
| `ANTHROPIC_API_KEY` | (unset) | Activates the Anthropic provider chain. |
| `OPENAI_API_KEY` | (unset) | Activates the OpenAI provider chain. |
| `OPENROUTER_API_KEY` | (unset) | Activates the OpenRouter (compatible) provider chain. |
| `OTTER_SHARE_URL` | `http://localhost:5174/api/shares` | Destination for `share --sink http`. See [`share.md`](share.md). |
| `OTTER_SERVER_TOKEN` | (unset) | When set, `chimera otter serve` requires `Authorization: Bearer <token>`. See [`server.md`](server.md). |
| `NO_COLOR` | (unset) | When set, force the plain output handler (synonym for `--no-color`). |

## What gets written to disk

| Path | What |
|---|---|
| `~/.chimera/eventlog/otter-<id>/summary.json` | Per-run metadata (model, cost, steps, success, prompt). |
| `~/.chimera/eventlog/otter-<id>/event-*.json` | Full event stream for `sessions show`. |
| `~/.chimera/shares/otter-<id>.<ext>` | Local share artifacts written by `share --sink file`. |
| `~/.chimera/credentials.json` | OAuth-issued tokens (mode `0o600`). |
| `~/.opencode/config.json` | Optional config ingest (read-only; otter never writes here). |

Everything is local and plaintext. No remote telemetry. To purge old runs:

```bash
rm -rf ~/.chimera/eventlog/otter-*
```

## Where to go next

- New to the provider chain? Start with [`providers.md`](providers.md).
- Need to pin a model in CI? See [`models.md`](models.md).
- Driving otter from a TUI / IDE / evals harness? Read [`server.md`](server.md).
- Sharing a session with a teammate? See [`share.md`](share.md).
- Debugging a past run? See [`sessions.md`](sessions.md).

---
title: Otter Quickstart
description: Install otter, run a one-shot turn, drop into the REPL, bring up the HTTP+SSE or ACP server, learn /undo, PTY routes, the remote skill marketplace, and per-session auth tokens.
---

# `chimera otter` Quickstart

`chimera otter` is the second Chimera coding-agent CLI. Where
[`chimera mink`](../mink/quickstart.md) mirrors a TUI-first agent,
otter mirrors a **server-first / multi-client open-source agent**: a
single ReAct loop you can drive from a one-shot CLI, an interactive
REPL, an HTTP server with SSE streaming, or an ACP JSON-RPC transport —
backed by the same `LoopConfig`, tool registry, and event-sourced
session store the rest of Chimera uses. Run `chimera otter --help-long`
to see verbose per-flag descriptions; `--help` itself stays under 50
lines for scannability.

This page walks the four entry points end-to-end. Deeper dives:

- [`providers.md`](providers.md) — provider chain (Anthropic, OpenAI, OpenRouter, Ollama, Modal, custom).
- [`models.md`](models.md) — model id catalogue + `OTTER_MODEL`.
- [`sessions.md`](sessions.md) — eventlog layout.
- [`share.md`](share.md) — share sinks and format.
- [`server.md`](server.md) — HTTP API + SSE event format.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- One of: an Anthropic API key, an OpenAI API key, an OpenRouter API
  key, a running Ollama daemon, or an Ollama Cloud account

```bash
uv --version                          # >= 0.4
uv sync --extra dev --extra anthropic # core + Anthropic SDK
```

The Anthropic extra is recommended because the otter default model is
`claude-sonnet-4-6`. To drive otter through OpenAI, OpenRouter, or
Ollama, swap the extra (`--extra openai`) or skip it; the
OpenAI-compatible adapter is stdlib + httpx.

## Provider configuration

Otter resolves the provider in this order (first match wins):

1. `--model <id>` on the CLI.
2. `$OTTER_MODEL` environment variable.
3. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
4. `$OPENROUTER_API_KEY` set → defaults to `anthropic/claude-sonnet-4`.
5. `$OPENAI_API_KEY` set → defaults to `gpt-4o`.
6. `$OLLAMA_API_KEY` set → defaults to `gpt-oss:120b-cloud`.
7. Friendly error pointing at the env vars above.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# OR
export OLLAMA_HOST=https://ollama.com
export OLLAMA_API_KEY=<your-key>
```

Or, for a local model:

```bash
ollama serve &
ollama pull qwen3:32b
chimera otter --model qwen3:32b -p "explain this repo"
```

## First one-shot turn

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

[otter] run saved as otter-20260514T091201-71032a5e at /Users/.../.chimera/eventlog/otter-20260514T091201-71032a5e/
```

Useful one-shot flags:

```bash
chimera otter --model gpt-oss:120b-cloud -p "draft a release note"
chimera otter --output-format json -p "ship it"          # one JSON blob
chimera otter --output-format stream-json -p "ship it"   # JSON-per-event
chimera otter --allowed-tools Read,Bash -p "audit"
chimera otter --no-save -p "ad-hoc, don't journal"
```

## Drop into the REPL

```bash
chimera otter
```

The REPL streams assistant text + tool calls inline, accepts mid-turn
steering, supports `Ctrl-C` cancellation, and exposes a slash-command
palette of 26 entries. Most useful ones:

```text
otter> /help               # full list
otter> /model              # show / cycle the active model
otter> /tools              # list available tools
otter> /agents             # list bundled subagent profiles
otter> /session save my-refactor
otter> /resume             # list recent persisted sessions
otter> /cost               # cumulative cost
otter> /mcp                # list connected MCP servers
otter> /undo --steps 3     # see below
otter> /edit               # open last assistant response in $EDITOR
otter> /share              # package the session for export
otter> /doctor             # environment health checks
otter> /exit
```

Each REPL session is event-sourced under
`~/.chimera/eventlog/otter-<utc>-<uuid>/`.

## File-undo (`/undo --steps N`)

`/undo` walks the FileTracker for the current session and reverses the
last N file writes / edits (default 1). Stops at the first conflict
(a file edited externally since the agent wrote it).

```text
otter> /undo --steps 3
/undo: reverted 3 changes
  ↶ src/util.py     (Edit at step 7)
  ↶ tests/test_util.py  (Write at step 5)
  ↶ src/util.py     (Edit at step 3)

otter> /undo --steps 5
/undo: reverted 2 changes; stopped at conflict
  ↶ tests/test_demo.py  (Write at step 11)
  ↶ chimera/foo.py      (Edit at step 9)
  ⚠ src/util.py was modified outside the session — refusing to revert
```

The session-local rollback log lives at
`~/.chimera/eventlog/otter-<id>/file-ops.json`.

## Bring up the HTTP server

For multi-client use — a separate TUI, an IDE plugin, an evals harness
— run otter as a headless HTTP server with REST + SSE streaming:

```bash
chimera otter serve --port 5173
```

Endpoints: `/sessions`, `/sessions/{id}`, `/sessions/{id}/turns`,
`/sessions/{id}/events` (SSE). Worked example with `curl`:

```bash
SID=$(curl -s -X POST http://localhost:5173/sessions \
        -H 'content-type: application/json' \
        -d '{"model":"claude-sonnet-4-6"}' | jq -r .id)

curl -N -X POST http://localhost:5173/sessions/$SID/turns \
     -H 'content-type: application/json' \
     -d '{"prompt":"list top-level files"}' &

curl -N http://localhost:5173/sessions/$SID/events
```

Sample SSE shape:

```text
event: text_delta
data: {"text":"I'll list "}

event: tool_call
data: {"name":"list_files","args":{"path":"."}}

event: turn_end
data: {"cost":0.0042,"steps":2}
```

Optional Bearer auth:

```bash
export OTTER_SERVER_TOKEN=$(openssl rand -hex 24)
chimera otter serve --port 5173
```

## ACP transport

`chimera otter serve --acp` boots a JSON-RPC 2.0 server over stdio —
the integration point for IDE clients that already speak ACP (Zed,
the upstream TUI, custom plugins).

```bash
chimera otter serve --acp
```

```json
{"jsonrpc":"2.0","id":1,"method":"prompt","params":{"text":"list files"}}
```

Response stream:

```json
{"jsonrpc":"2.0","method":"event","params":{"type":"text_delta","text":"I'll "}}
{"jsonrpc":"2.0","method":"event","params":{"type":"tool_call","name":"list_files"}}
{"jsonrpc":"2.0","id":1,"result":{"text":"...","cost":0.0042}}
```

Methods: `prompt`, `steer`, `cancel`, `get_state`, `compact`,
`list_sessions`, `resume`. Schema in [`acp.md`](acp.md).

## Per-session auth tokens

The HTTP server supports two token granularities:

| Scope | Header | Lifecycle |
|---|---|---|
| Process-wide | `Authorization: Bearer $OTTER_SERVER_TOKEN` | Set once at boot. |
| Per-session | `Authorization: Bearer <session-token>` | Issued by `POST /sessions`, valid for that session only. |

Per-session tokens land in the `POST /sessions` response body
(`{"id":"otter-...","token":"st_..."}`). Use one instead of the
process-wide token so an IDE plugin can multiplex sessions through one
server without granting blanket access.

## PTY routes (terminal proxy)

Long-running TUI commands (`vim`, `top`, `watch`) can be routed
through a PTY so the agent's `Bash` tool stays responsive:

```bash
chimera otter serve --pty --port 5173

# Inside the REPL, long-running commands route transparently:
otter> watch -n1 'uv run pytest -x'
[pty otter-...] streaming…
```

PTY traffic is multiplexed over the same SSE stream. See
[`server.md`](server.md) for the wire shape.

## Remote skill marketplace

`chimera otter skills` walks a remote registry of community-contributed
skill packs (markdown system-prompt overlays):

```bash
chimera otter skills list                    # discoverable packs
chimera otter skills install rag-grounding
chimera otter skills info rag-grounding
chimera otter skills uninstall rag-grounding
```

Installed skills land under `~/.chimera/skills/<name>/` and auto-mount
on every otter run. See [`skills.md`](skills.md).

## Inspecting and sharing sessions

```bash
chimera otter sessions list
chimera otter sessions show otter-20260514T091201-71032a5e
chimera otter sessions list --since 7d --json
```

Package a session as HTML / Markdown / JSON and route it:

```bash
chimera otter share otter-20260514T091201-71032a5e
chimera otter share <id> --sink http --format json
chimera otter share <id> --sink stdout --format md | less
```

## Choose your model

Recommended models for the server-first posture (low-latency first
token, streaming-friendly tool calls):

| Backend | Tag | Why for otter |
|---|---|---|
| Anthropic | `claude-sonnet-4-6` | Default; strongest tool calling + streaming. |
| Ollama Cloud | `gpt-oss:120b-cloud` | Free w/ Ollama account; native tools; fast first token. |
| OpenRouter | `anthropic/claude-sonnet-4` | Same model via the OpenRouter free-tier. |
| OpenAI | `gpt-4o` | Solid baseline; needs `$OPENAI_API_KEY`. |

See [the Ollama Cloud recipe](../use-with-ollama.md) for the auth
handshake.

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `OTTER_MODEL` | (unset) | Default model id. |
| `ANTHROPIC_API_KEY` | (unset) | Anthropic chain. |
| `OPENAI_API_KEY` | (unset) | OpenAI chain. |
| `OPENROUTER_API_KEY` | (unset) | OpenRouter chain. |
| `OLLAMA_API_KEY` | (unset) | Ollama Cloud (`:cloud` tags). |
| `OLLAMA_HOST` | `http://localhost:11434` | Daemon URL. |
| `OTTER_SHARE_URL` | `http://localhost:5174/api/shares` | Destination for `share --sink http`. |
| `OTTER_SERVER_TOKEN` | (unset) | Process-wide Bearer token for `otter serve`. |
| `NO_COLOR` | (unset) | Plain output handler. |

## What gets written to disk

| Path | What |
|---|---|
| `~/.chimera/eventlog/otter-<id>/summary.json` | Per-run metadata. |
| `~/.chimera/eventlog/otter-<id>/file-ops.json` | Per-session undo log. |
| `~/.chimera/eventlog/otter-<id>/event-*.json` | Event stream. |
| `~/.chimera/shares/otter-<id>.<ext>` | Local share artifacts. |
| `~/.chimera/skills/<name>/` | Installed remote skill packs. |
| `~/.chimera/credentials.json` | OAuth tokens (mode `0o600`). |

Everything is local-only. Purge with `rm -rf ~/.chimera/eventlog/otter-*`.

## TUI default (wave 11)

Bare `chimera otter` picks Textual TUI when stdout is a TTY *and* the
optional `[tui]` extra is installed; otherwise the readline REPL.
Force with `--no-tui` / `--tui` or `CHIMERA_NO_TUI=1`. See
[`tui.md`](tui.md) for the auto-launch decision tree.

## Where to go next

- [Providers](./providers.md) and [Models](./models.md).
- [Sessions](./sessions.md) and [Share](./share.md).
- [Server](./server.md) — the HTTP+SSE wire format.
- [ACP](./acp.md) — JSON-RPC transport.
- [Skills](./skills.md) — remote skill marketplace.
- [Security and Trademarks](./security-and-trademarks.md) — policy.

---

### Verified (2026-05-14)

Two commands from this quickstart, against Ollama Cloud:

```text
$ OLLAMA_HOST=https://ollama.com OLLAMA_API_KEY=*** \
    chimera otter -p "Hello, please reply with one word: hello" \
                  --model gpt-oss:120b-cloud --max-steps 2 --no-color --no-save
hello

$ chimera otter --version
chimera otter 0.7.0
```

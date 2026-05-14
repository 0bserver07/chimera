---
title: Ferret Quickstart
description: Install ferret, walk the three sandbox modes side-by-side, learn the approval matrix, bring up the IDE ACP transport, the cloud bridge, the mcp-server subcommand, and the apply/review/fork helpers.
---

# `chimera ferret` Quickstart

`chimera ferret` is the third Chimera coding-agent CLI. Where
[`chimera mink`](../mink/quickstart.md) mirrors a TUI-first agent and
[`chimera otter`](../otter/quickstart.md) mirrors a server-first
multi-client agent, ferret mirrors the **IDE-first OpenAI-flagship
coding agent**: a sandbox-first runner with single-flag approval
presets, an ACP JSON-RPC transport that ships as the default `serve`
transport, an optional cloud bridge so a local ferret session can be
driven from a remote UI, and three helper subcommands (`apply`,
`review`, `fork`) for agent-assisted patch operations.

Deeper dives:

- [`providers.md`](providers.md) — OpenAI-flagship provider chain.
- [`sandbox.md`](sandbox.md) — three sandbox modes.
- [`approval.md`](approval.md) — approval presets.
- [`ide.md`](ide.md) — ACP schema + notification kinds.
- [`cloud-bridge.md`](cloud-bridge.md) — bridge setup.
- [`parity-matrix.md`](parity-matrix.md) — surface mapping.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- One of: an OpenAI key, an Anthropic key, an OpenRouter key, an
  Ollama daemon, or an Ollama Cloud account

```bash
uv --version                         # >= 0.4
uv sync --extra dev --extra openai   # core + OpenAI SDK
```

## Provider configuration

Ferret resolves the provider in this order (first match wins):

1. `--model <id>` on the CLI.
2. `$FERRET_MODEL` environment variable.
3. `$OPENAI_API_KEY` set → defaults to `gpt-5` (falls back to `gpt-4o`).
4. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
5. `$OPENROUTER_API_KEY` set → defaults to `openai/gpt-5`.
6. `$OLLAMA_API_KEY` set → defaults to `gpt-oss:120b-cloud`.
7. Friendly error pointing at the env vars above.

```bash
export OPENAI_API_KEY=sk-...
# OR — free path with an Ollama account
export OLLAMA_HOST=https://ollama.com
export OLLAMA_API_KEY=<your-key>
```

## First one-shot turn

Ferret defaults to **read-only** sandbox mode, so the first run is
safe to point at any directory:

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

[ferret] run saved as ferret-20260514T101455-1f3c2a8b at /Users/.../.chimera/eventlog/ferret-...
```

The `[ferret] sandbox=... approval=...` banner on the first line tells
you exactly which guardrails are active.

## Sandbox modes side-by-side

Three sandbox modes, each with a distinct OS-level posture. All three
fix at process start because they install kernel-level hooks.

| Mode | Reads | Writes | Network | Notes |
| --- | --- | --- | --- | --- |
| `read-only` | yes | **denied** | **denied** | Default. Safe on any cwd. |
| `workspace-write` | yes | inside cwd only | **denied** | Writes outside cwd are EACCES. |
| `workspace-write-network` | yes | inside cwd only | yes | Add network on top of `workspace-write`. |

Worked invocations for each:

```bash
# Safest possible — only reads.
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

Sandbox modes are documented in [`sandbox.md`](sandbox.md).

## Approval matrix

Approval is orthogonal to the sandbox. The sandbox blocks at the OS
level; the approval preset blocks at the policy level. **A tool call
has to pass both.**

| Preset | Reads | Edits | Bash / Git | Network |
| --- | --- | --- | --- | --- |
| `read-only` | allow | **ask** | **ask** | **ask** |
| `auto` | allow | allow | ask | ask |
| `full` | allow | allow | allow | allow |

Compose with sandbox:

| Sandbox / Approval | `read-only` | `auto` | `full` |
| --- | --- | --- | --- |
| `read-only` | reads only | reads only | reads only |
| `workspace-write` | reads + asked writes | edits in cwd, asked bash | edits + bash in cwd |
| `workspace-write-network` | + asked net | + asked net | full |

You can change the approval preset mid-session in the REPL with
`/approval auto`. The sandbox mode is fixed at process start; restart
ferret with a different `--sandbox` flag to widen.

## Drop into the REPL

```bash
chimera ferret
chimera ferret --sandbox workspace-write --approval auto
```

The REPL streams assistant text + tool calls inline, accepts mid-turn
steering, supports `Ctrl-C` cancellation, and ships three
ferret-specific entries on top of the standard palette:

```text
ferret> /sandbox
sandbox=workspace-write
ferret> /approval
approval=auto
ferret> /approval read-only
approval changed: auto → read-only
ferret> /bridge
bridge: not connected
```

Type `/help` for the full list (24 entries).

## `apply` / `review` / `fork` subcommands

Three helpers for agent-assisted patch operations:

```bash
# apply — agent generates a patch from a description, applies cleanly
chimera ferret apply -m "add a CLI flag --max-tokens to the API client"

# review — agent reviews uncommitted changes and emits a structured report
chimera ferret review
chimera ferret review --staged-only
chimera ferret review --since HEAD~3

# fork — clone a session, mutate one prompt, run again
chimera ferret fork ferret-20260514T101455-1f3c2a8b \
                    --prompt-override "fix the bug, but use httpx instead of requests"
```

Each subcommand reuses the loop + sandbox + approval state; only the
prompt and the bookkeeping differ. See [`subcommands.md`](subcommands.md)
for the JSON envelopes when piped.

## ACP server (default `serve` transport)

Ferret's `serve` defaults to **ACP over stdio**, not HTTP. This is
the IDE-first stance: most ferret users drive ferret from an IDE
plugin (Zed, VS Code) that already speaks ACP.

```bash
chimera ferret serve                 # ACP over stdio (default)
chimera ferret serve --http --port 5173    # HTTP, opt-in
```

ACP wire example:

```json
{"jsonrpc":"2.0","id":1,"method":"prompt","params":{"text":"list files"}}
```

```json
{"jsonrpc":"2.0","method":"notification","params":{"kind":"tool_call","name":"list_files"}}
{"jsonrpc":"2.0","method":"notification","params":{"kind":"text_delta","text":"I'll "}}
{"jsonrpc":"2.0","id":1,"result":{"text":"...","cost":0.0042}}
```

Notification kinds (`tool_call`, `tool_result`, `text_delta`,
`turn_end`, `approval_requested`, `approval_decided`) and a worked
Zed + VS Code recipe live in [`ide.md`](ide.md).

## `mcp-server` subcommand

Ferret can be exposed *as* an MCP server (in addition to consuming
external MCP servers via `.mcp.json`). This is how another agent — say
a `chimera mink` instance — drives ferret as a sandboxed sub-agent.

```bash
chimera ferret mcp-server                 # stdio (default)
chimera ferret mcp-server --port 5174     # HTTP
```

Exposed tools: `ferret_run`, `ferret_apply`, `ferret_review`. Add
ferret to a calling agent's `.mcp.json`:

```json
{
  "servers": {
    "ferret-sandbox": {
      "command": ["chimera", "ferret", "mcp-server"],
      "env": {"FERRET_SANDBOX": "workspace-write", "FERRET_APPROVAL": "auto"}
    }
  }
}
```

## Cloud bridge

When you want a remote UI (a teammate's web dashboard, a phone, an
on-call console) to drive a local ferret session, forward the ACP
transport over an authenticated HTTPS bridge:

```bash
chimera ferret bridge --remote-url https://ferret.example.com \
                      --auth-token "$FERRET_BRIDGE_TOKEN"
```

Inside the REPL, `/bridge` reports status. Reconnect semantics,
remote-URL contract, and token format live in
[`cloud-bridge.md`](cloud-bridge.md). The bridge is optional — local
ACP / HTTP servers cover the standard cases.

## Choose your model

Recommended models for the IDE-first / sandboxed posture (low-latency
plan + edit cycles, frequent diff generation):

| Backend | Tag | Why for ferret |
|---|---|---|
| OpenAI | `gpt-5` / `gpt-4o` | Default; tuned for the IDE-first ergonomic. |
| Anthropic | `claude-sonnet-4-6` | Strong diff fidelity; fallback. |
| Ollama Cloud | `gpt-oss:120b-cloud` | Free with Ollama account; native tools. |
| OpenRouter | `openai/gpt-5` | Same model via OpenRouter aggregator. |

See [the Ollama Cloud recipe](../use-with-ollama.md) for the auth
handshake.

## Ferret config file

Ferret ingests `~/.codex/config.toml` as a filesystem fact: when the
file exists, ferret picks up `model`, `provider`, `sandbox`,
`approval`, and `mcp` sections that map onto Chimera primitives. CLI
flags always win over the config file, which always wins over env
vars. See [`parity-matrix.md`](parity-matrix.md) for the full key map.

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `FERRET_MODEL` | (unset) | Default model id. |
| `OPENAI_API_KEY` | (unset) | OpenAI chain (default for ferret). |
| `ANTHROPIC_API_KEY` | (unset) | Anthropic chain. |
| `OPENROUTER_API_KEY` | (unset) | OpenRouter chain. |
| `OLLAMA_API_KEY` | (unset) | Ollama Cloud (`:cloud` tags). |
| `OLLAMA_HOST` | `http://localhost:11434` | Daemon URL. |
| `FERRET_BRIDGE_TOKEN` | (unset) | Bearer for `ferret bridge`. |
| `FERRET_SANDBOX` | (unset) | Default sandbox mode. |
| `FERRET_APPROVAL` | (unset) | Default approval preset. |
| `NO_COLOR` | (unset) | Plain output handler. |

## Where to go next

- [Sandbox](./sandbox.md) and [Approval](./approval.md) — they compose.
- [IDE](./ide.md) — ACP schema and Zed / VS Code recipe.
- [Cloud bridge](./cloud-bridge.md).
- [Parity Matrix](./parity-matrix.md).
- [Security and Trademarks](./security-and-trademarks.md).

---

### Verified (2026-05-14)

Two commands from this quickstart, against Ollama Cloud:

```text
$ OLLAMA_HOST=https://ollama.com OLLAMA_API_KEY=*** \
    chimera ferret -p "Hello, please reply with one word: hello" \
                   --model gpt-oss:120b-cloud --max-steps 2 --no-color
hello

$ chimera ferret --version
chimera ferret 0.7.0
```

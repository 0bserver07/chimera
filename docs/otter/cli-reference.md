---
title: Otter CLI Reference
description: Canonical reference for `chimera otter` and every subcommand — synopsis, flags, exit codes, and example invocations, generated from the live `--help` output.
---

# `chimera otter` CLI Reference

This page is the canonical reference for the `chimera otter` command-line. It mirrors the live `chimera otter --help` output verbatim and adds per-subcommand flag tables for the action-specific options that are parsed off `args` by each dispatcher.

For a guided tour, see [`quickstart.md`](quickstart.md). For deeper context on any one surface, follow the cross-links at the end of each section.

## Synopsis

```text
chimera otter [-h] [--version] [--model MODEL] [-p PRINT_MODE]
              [--output-format {text,json,stream-json}]
              [--max-steps MAX_STEPS] [--cwd CWD]
              [--allowed-tools ALLOWED_TOOLS] [--no-rich] [--no-color]
              [--no-save] [--no-lsp] [--no-rules]
              [--no-custom-commands] [--run-id RUN_ID] [--acp]
              [--host HOST] [--port PORT] [--auth-token AUTH_TOKEN]
              [--tls-cert TLS_CERT] [--tls-key TLS_KEY] [--no-plugins]
              [--no-mcp] [--bench-limit BENCH_LIMIT]
              [--bench-domain BENCH_DOMAIN]
              [SUBCOMMAND] [ACTION] [TARGET]
```

`SUBCOMMAND` is one of `serve`, `sessions`, `share`, `agents`, `bench`. Omitting it drops you into the interactive REPL (or runs a single turn when `-p` is set).

`ACTION` and `TARGET` are reused across subcommands (e.g. `sessions list`, `sessions show <id>`, `share <id>`, `agents show <name>`, `bench humaneval`).

## Positional arguments

| Argument | Choices | Meaning |
|---|---|---|
| `SUBCOMMAND` | `serve`, `sessions`, `share`, `agents`, `bench` | Pick a non-REPL entry point. Optional. |
| `ACTION` | `list`, `show`, `humaneval`, `tau-bench` | With `sessions` or `agents`: `list` or `show <name>`. With `bench`: the benchmark name. |
| `TARGET` | (free-form) | Run/session id consumed by `show` or `share` actions. |

## Top-level flags

These flags apply to every entry point. Where a flag is only meaningful for one subcommand, the description says so.

| Flag | Default | Description |
|---|---|---|
| `-h, --help` | — | Show the help message and exit. |
| `--version` | — | Show the program's version number and exit. |
| `--model MODEL` | `$OTTER_MODEL` or `claude-sonnet-4-6` | Model identifier. Resolved through `chimera.providers.factory.create_provider`. See [`providers.md`](providers.md) and [`models.md`](models.md). |
| `-p, --print PRINT_MODE` | (unset) | One-shot: run a single turn with `PROMPT`, print, exit. |
| `--output-format {text,json,stream-json}` | `text` | One-shot output format. `stream-json` prints one JSON line per `LoopEvent`; `json` prints a single result object on exit. |
| `--max-steps MAX_STEPS` | `50` | Maximum agent steps per turn. |
| `--cwd CWD` | current directory | Working directory. |
| `--allowed-tools ALLOWED_TOOLS` | (empty = all) | Comma-separated tool names to allow (case-insensitive). |
| `--no-rich` | off | Force the plain `ConsoleStreamHandler` even when stdout is a TTY. Default: auto-select rich on TTY, plain when piped. |
| `--no-color` | off | Synonym for `--no-rich`. Also honored implicitly when `$NO_COLOR` is set. |
| `--no-save` | off | Do not persist the one-shot run to `~/.chimera/eventlog/`. Default behavior saves the full message + tool history. |
| `--no-lsp` | off | Disable the otter LSP tool group (diagnostics / completion / rename / definition / references). LSP is on by default; tools degrade gracefully when no language server is found. |
| `--no-rules` | off | Skip ingestion of `AGENTS.md`, `.cursor/rules/*.mdc`, and `.opencode/rules.md` into the system prompt. Default behavior appends a `## Project Rules` section when any source exists. |
| `--no-custom-commands` | off | Skip loading user-defined commands from `.opencode/command/*.md` at REPL startup. Default behavior loads both user-scope (`~/.opencode/command/`) and project-scope ones. |
| `--run-id RUN_ID` | auto-generated | Override the auto-generated run id for the persisted eventlog directory. Useful for reproducible test fixtures. |
| `--acp` | off | With `serve`: run the ACP (Agent Client Protocol) JSON-RPC server on stdio instead of the HTTP server. |
| `--host HOST` | `127.0.0.1` | With `serve` (HTTP mode): bind host. Use `0.0.0.0` only with `--auth-token`. |
| `--port PORT` | `5173` | With `serve` (HTTP mode): bind port. |
| `--auth-token AUTH_TOKEN` | (unset) | With `serve` (HTTP mode): shared-secret bearer token required on every request except `/healthz`. |
| `--tls-cert TLS_CERT` | (unset) | With `serve` (HTTP mode): path to a PEM-encoded server certificate. Must be paired with `--tls-key`. When set the server serves HTTPS. |
| `--tls-key TLS_KEY` | (unset) | With `serve` (HTTP mode): PEM-encoded private key matching `--tls-cert`. |
| `--no-plugins` | off | Skip directory-based plugin discovery under `~/.opencode/plugin/*` and `<project>/.opencode/plugin/*` (default: load all discovered plugins). |
| `--no-mcp` | off | Skip MCP server discovery from `~/.opencode/config.json` and `.opencode/{config,mcp}.json`. Default: MCP servers are loaded and their tools attached to the otter agent. |
| `--bench-limit BENCH_LIMIT` | `5` | With `bench`: max tasks to run. Pass `0` for a full run. |
| `--bench-domain BENCH_DOMAIN` | `airline` | With `bench tau-bench`: domain to evaluate (`airline`, `retail`, `telecom`, `banking`, `mock`). |

## Environment variables

| Variable | Effect |
|---|---|
| `OTTER_MODEL` | Default model id when `--model` is unset. See [`models.md`](models.md). |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` | Activate provider chains. See [`providers.md`](providers.md). |
| `OTTER_SHARE_URL` | Destination for `share --sink http`. Default `http://localhost:5174/api/shares`. |
| `OTTER_SERVER_TOKEN` | When set, `chimera otter serve` requires `Authorization: Bearer <token>`. See [`server.md`](server.md). |
| `NO_COLOR` | Force plain handler (synonym for `--no-color`). |

## REPL (no subcommand)

```bash
chimera otter
```

Drops into the interactive REPL. Streams assistant text + tool calls inline, accepts mid-turn steering, supports `Ctrl-C` cancellation, and exposes a slash-command palette. Type `/help` at the prompt for the live list. See [`quickstart.md`](quickstart.md) and [`slash-commands.md`](slash-commands.md).

## One-shot (`-p` / `--print`)

```bash
chimera otter -p "list the top-level files and read the README"
chimera otter --model gpt-4o -p "draft a release note"
chimera otter --output-format json -p "ship it"
chimera otter --output-format stream-json -p "ship it"
chimera otter --max-steps 10 -p "summarize"
chimera otter --allowed-tools Read,Bash -p "audit"
chimera otter --no-save -p "ad-hoc, don't journal"
chimera otter --no-color -p "..." | tee otter.log
```

`-p` runs a single turn and exits. Useful for scripting, CI, and pipes. Output is text by default; pass `--output-format json` for a single result blob, or `--output-format stream-json` for one JSON line per `LoopEvent`.

See [`quickstart.md`](quickstart.md) for output shapes and [`sessions.md`](sessions.md) for the persisted run directory layout.

## `chimera otter serve`

Bring up otter as a headless multi-client server. HTTP by default; ACP JSON-RPC over stdio with `--acp`.

```bash
chimera otter serve
chimera otter serve --port 5173
chimera otter serve --host 0.0.0.0 --auth-token "$(openssl rand -hex 32)"
chimera otter serve --tls-cert ./server.pem --tls-key ./server.key
chimera otter serve --acp
```

| Flag | Default | Notes |
|---|---|---|
| `--host HOST` | `127.0.0.1` | Bind host. Use `0.0.0.0` only with `--auth-token`. |
| `--port PORT` | `5173` | Bind port. |
| `--auth-token AUTH_TOKEN` | (unset) | Shared-secret bearer token required on every request except `/healthz`. Falls back to `$OTTER_SERVER_TOKEN`. |
| `--tls-cert TLS_CERT` | (unset) | PEM cert. Pair with `--tls-key` to serve HTTPS. |
| `--tls-key TLS_KEY` | (unset) | PEM key matching `--tls-cert`. |
| `--acp` | off | JSON-RPC 2.0 over stdio. Mutually exclusive with HTTP flags. |

REST endpoints (`/sessions`, `/sessions/{id}`, `/sessions/{id}/turns`, SSE `/sessions/{id}/events`), the SSE event format, and the auth model live in [`server.md`](server.md). The ACP method set is in [`acp.md`](acp.md).

## `chimera otter sessions`

Browse and inspect the eventlog under `~/.chimera/eventlog/otter-*/`.

```bash
chimera otter sessions list
chimera otter sessions list --limit 5
chimera otter sessions list --since 7d
chimera otter sessions list --since 24h
chimera otter sessions list --since 2026-04-01
chimera otter sessions list --model claude-sonnet-4-6
chimera otter sessions list --json
chimera otter sessions show otter-20260425T091201-71032a5e
chimera otter sessions show <id> --json
chimera otter sessions show <id> --full
```

| Action | Flag | Default | Description |
|---|---|---|---|
| `list` | `--limit N` | `50` | Cap on rows printed. |
| `list` | `--since <duration|date>` | (unset) | Drop sessions older than `Ns`/`Nm`/`Nh`/`Nd`/`Nw` or an ISO date. |
| `list` | `--model NAME` | (unset) | Exact match against `summary.json`'s `model` field. |
| `list` | `--json` | off | Emit `SessionRecord.to_dict()` as a JSON array. |
| `show <id>` | `--json` | off | Emit the full record as a single JSON object. |
| `show <id>` | `--full` | off | Print every event inline rather than a summary. |

Full layout, summary schema, and resume guidance in [`sessions.md`](sessions.md).

## `chimera otter share`

Package a persisted session into a portable transcript and route it to a sink.

```bash
chimera otter share <session-id>
chimera otter share <session-id> --sink file --format md
chimera otter share <session-id> --sink stdout --format json | jq .
chimera otter share <session-id> --sink http
chimera otter share <session-id> --sink http --url https://collector.internal/shares
```

| Flag | Choices | Default | Description |
|---|---|---|---|
| `--sink` | `file`, `http`, `stdout` | `file` | Where to send the rendered transcript. |
| `--format` | `html`, `md`, `json` | `html` | Serialization format. `file` sink picks an extension to match. |
| `--url URL` | (URL) | `$OTTER_SHARE_URL` or `http://localhost:5174/api/shares` | Override the HTTP collector for `--sink http`. |

Exit codes, share schema, and privacy notes in [`share.md`](share.md).

## `chimera otter agents`

List or inspect agent presets resolved from project (`./agents/*.md`), user (`~/.opencode/agent/*.md`), and built-in scopes.

```bash
chimera otter agents
chimera otter agents list
chimera otter agents show reviewer
```

| Action | Description |
|---|---|
| (no action) | Defaults to `list`. |
| `list` | Three-column table: name, scope, description. |
| `show <name>` | Print the merged frontmatter as YAML. |

Schema, scope precedence, and built-in presets in [`agents.md`](agents.md).

## `chimera otter bench`

Run a benchmark adapter against an otter Agent constructed with otter defaults. Two benchmarks are wired:

```bash
chimera otter bench humaneval
chimera otter bench humaneval --bench-limit 20
chimera otter bench humaneval --bench-limit 0          # full HumanEval (164 tasks)
chimera otter bench tau-bench
chimera otter bench tau-bench --bench-domain retail
chimera otter bench tau-bench --bench-domain mock --bench-limit 10
```

| Flag | Default | Description |
|---|---|---|
| `--bench-limit N` | `5` | Max tasks to run. `0` means "full run." Default of 5 keeps an unguarded `bench humaneval` cheap. |
| `--bench-domain {airline,retail,telecom,banking,mock}` | `airline` | Only meaningful with `tau-bench`. |

`humaneval` runs out-of-the-box. `tau-bench` requires a local dataset staged under `~/.chimera/datasets/tau-bench/`; the runner emits a clear `NotImplementedError` with staging instructions when the dataset is absent.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Run failed (provider error, agent error, benchmark error). |
| `2` | Usage error: unknown subcommand/action, missing target id, unknown `--sink` / `--format` / `--since`, unknown allowed-tool name. |

## See also

- [`quickstart.md`](quickstart.md) — five-minute install + first turn.
- [`providers.md`](providers.md) — provider chain (Anthropic, OpenAI, OpenRouter, Ollama, Modal).
- [`models.md`](models.md) — model id catalogue + `OTTER_MODEL`.
- [`server.md`](server.md) — `chimera otter serve` HTTP API + SSE event format.
- [`sessions.md`](sessions.md) — eventlog layout and `sessions list` / `sessions show`.
- [`share.md`](share.md) — `chimera otter share` sinks and formats.
- [`agents.md`](agents.md) — agent presets and frontmatter schema.
- [`slash-commands.md`](slash-commands.md) — REPL slash-command palette.
- [`acp.md`](acp.md) — ACP JSON-RPC method set for `serve --acp`.

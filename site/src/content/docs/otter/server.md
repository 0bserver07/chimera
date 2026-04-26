---
title: Otter Server
description: chimera otter serve HTTP API surface — REST endpoints, SSE event streaming, and the OTTER_SERVER_TOKEN auth model.
---

# `chimera otter serve`

`chimera otter serve` runs otter as a headless HTTP server. The same ReAct
loop, tool registry, provider chain, and event-sourced session store the
CLI uses are exposed over a small REST + Server-Sent-Events (SSE)
surface, so a separate TUI client, an IDE plugin, an evals harness, or a
multi-tenant front-end can drive otter sessions over the network.

For an alternative transport — JSON-RPC over stdio, suitable for IDE
clients that already speak the Agent Client Protocol — pass `--acp`. See
the bottom of this page for the ACP transport notes.

This doc covers:

- The CLI flag surface for `serve`.
- The REST endpoints and SSE event format.
- The `OTTER_SERVER_TOKEN` Bearer-auth model.
- A worked client example.

## Usage

```bash
chimera otter serve [--port <int>] [--host <str>] [--cors <origin>] [--acp]
```

| Flag | Description | Default |
|---|---|---|
| `--port` | Port to listen on. | `5173` |
| `--host` | Hostname / interface to bind. | `127.0.0.1` |
| `--cors` | Browser origin to allow (repeatable). | `[]` (none) |
| `--acp` | Run the ACP JSON-RPC server on stdio instead of HTTP. | `false` |

The default bind is **loopback only**. To expose otter on a LAN, pass
`--host 0.0.0.0` and set `OTTER_SERVER_TOKEN` (see
[Authentication](#authentication)). Multiple `--cors` flags are allowed
when you need to drive otter from a browser app:

```bash
chimera otter serve --cors http://localhost:3000 --cors https://app.example.com
```

The server holds **one provider for the lifetime of the process**, the
same way the REPL does. To fan out across providers, run multiple
servers on different ports.

## Authentication

`OTTER_SERVER_TOKEN` toggles HTTP Bearer-auth on every endpoint:

```bash
export OTTER_SERVER_TOKEN=your-secret-token
chimera otter serve --port 5173
```

When set, every request must carry:

```http
Authorization: Bearer your-secret-token
```

Missing or mismatched tokens return `401 Unauthorized` with a JSON body:

```json
{"error": "unauthorized", "message": "missing or invalid bearer token"}
```

When `OTTER_SERVER_TOKEN` is unset, the server is open. The default
loopback bind (`127.0.0.1`) is intentionally narrow so an unset token
does not accidentally expose the server to the LAN. **Always set the
token if you change `--host`.**

## Endpoints

All requests/responses are `application/json` unless noted. UTC ISO-8601
strings everywhere. Session ids match the directory format on disk
(`otter-<utc>-<uuid>`). See [`sessions.md`](sessions.md) for the on-disk
schema.

### `GET /health`

Liveness probe. No auth required even when `OTTER_SERVER_TOKEN` is set —
this lets a load balancer poll without holding the secret.

```json
{"status": "ok", "version": "0.3.0", "started_at": "2026-04-25T09:00:00Z"}
```

### `GET /info`

Server self-description. Auth required.

```json
{
  "version": "0.3.0",
  "model": "claude-sonnet-4-6",
  "provider": "anthropic",
  "tools": ["Read", "Write", "Edit", "Bash", "search", "list_files", "..."],
  "cwd": "/Users/yad/repos/chimera"
}
```

### `GET /sessions`

List persisted sessions. Mirrors `chimera otter sessions list`. Query
params: `limit` (default `20`), `since` (`Nd` / `Nh` / ISO-8601
cutoff), `model` (exact-match filter). Response wraps the list under
`"sessions"`; each entry carries `session_id`, `started_at`,
`ended_at`, `model`, `prompt`, `success`, `cost_usd`, `steps`,
`tool_calls`.

### `GET /sessions/{id}`

Load a single session, summary plus every event. Same shape as
`chimera otter sessions show --json` — a JSON object with
`session_id`, `summary`, and `events`. `404 Not Found` when the id
does not exist.

### `POST /sessions`

Create a new session. Body: `{"prompt": "...", "model": "...",
"max_steps": 50, "cwd": "/abs/path", "allowed_tools": ["Read", "Bash"]}`.
`prompt` is required; other fields are optional and fall back to
launch-time defaults. Response: `{"session_id": "...", "status":
"running"}`. The call returns immediately; stream events from
`GET /sessions/{id}/events` (SSE) and / or poll `GET /sessions/{id}`
for the final summary.

### `POST /sessions/{id}/turns`

Extend an existing session with another user turn. Body:
`{"prompt": "..."}`. Response: same shape as `POST /sessions`.

### `POST /sessions/{id}/cancel`

Cooperatively cancel an in-flight turn. The server sets the session's
`CancellationToken` and returns `204 No Content`. Streamed events for
the cancelled turn end with a `cancelled` event.

### `GET /sessions/{id}/events` — SSE

Server-Sent Events stream of every event the agent emits for this
session, including events already journaled. Use the standard
`Last-Event-ID` header to resume.

Headers on the response:

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
```

#### SSE event format

Each emitted event is one SSE record:

```
id: 42
event: <event-type>
data: {"id": "evt-...", "type": "<event-type>", "metadata": { … }, "ts": "2026-04-25T09:12:08Z"}

```

Notes:

- `id:` is the per-session event counter (matches the
  `event-NNNNNN-*.json` filename's counter).
- `event:` is the canonical event type
  (`text_delta`, `tool_call`, `tool_result`, `step_start`, `step_end`,
  `turn_start`, `turn_end`, `agent_result`, `error`, `cancelled`).
- `data:` is a single JSON object on one line. Use
  `JSON.parse(messageEvent.data)` directly.
- A blank line terminates the record (per the SSE spec).

The stream stays open until the session emits `agent_result` or
`error` / `cancelled`. After that, the server sends:

```
event: end
data: {"session_id": "...", "success": true}

```

…then closes the connection. Clients that want to keep the connection
warm for the next turn should re-open after `end`.

#### Replay vs live

By default the SSE stream replays every persisted event, then continues
live. To skip replay and start at "now", pass `?from=live`. To resume
from a specific counter, pass the `Last-Event-ID` header (standard SSE
resume); the server replays from `<counter+1>` onward.

### `GET /sessions/{id}/transcript`

Render a session as HTML / Markdown / JSON, the same renderings the
[`share`](share.md) command produces. Query: `?format=html|md|json`
(default `html`). Response `Content-Type` matches `text/html`,
`text/markdown`, or `application/json`.

This endpoint is read-only — it does not POST to any external collector
even when `$OTTER_SHARE_URL` is set. It's the server-side answer to
"give me this transcript so I can render it in my UI."

### `POST /sessions/{id}/share`

Trigger a share dispatch via the same code path as `chimera otter share`.
Body:

```json
{"sink": "http", "format": "json", "url": "https://collector.example.com/api/shares"}
```

`sink` is one of `file` / `http` / `stdout` (when `stdout`, the rendered
body is returned in the JSON response). `format` is `html` / `md` /
`json`. The response carries the resulting path / endpoint reply / body
depending on the sink. Errors map to `400` (validation) or `502`
(upstream failure).

### `GET /providers`

Server self-description: `{"active": {"provider": "...", "model": "..."},
"available": [{"name": "...", "configured": true}, ...]}`. Useful to
let a client populate a model picker without re-discovering env vars.

### `GET /tools`

List the tools the active session group exposes. One entry per tool with
its `name`, `description`, and JSON schema (the
`to_anthropic_schema()` shape).

## Worked example: drive a session from `curl`

```bash
# 1. Start the server with auth on.
export OTTER_SERVER_TOKEN=dev-secret
chimera otter serve --port 5173 &

# 2. Open a session.
SID=$(curl -s -X POST http://127.0.0.1:5173/sessions \
  -H "Authorization: Bearer dev-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "list files and read README"}' \
  | jq -r '.session_id')

# 3. Stream events (SSE) until the run completes.
curl -N \
  -H "Authorization: Bearer dev-secret" \
  -H "Accept: text/event-stream" \
  http://127.0.0.1:5173/sessions/"$SID"/events

# 4. Inspect the final summary.
curl -s -H "Authorization: Bearer dev-secret" \
  http://127.0.0.1:5173/sessions/"$SID" | jq .summary
```

Browser clients: the standard `EventSource` API does not accept custom
headers, so the bearer token cannot ride on the SSE connection
directly. Terminate TLS at a reverse proxy that injects `Authorization`
for the SSE path, or use a `fetch`-based SSE polyfill.

## ACP transport (`--acp`)

`chimera otter serve --acp` swaps the HTTP transport for a JSON-RPC 2.0
server speaking the Agent Client Protocol over stdin/stdout — the
shape IDE clients (Zed and others) already understand for an "external
agent" handshake.

Methods exposed: `initialize` (handshake), `session/new`,
`session/turn`, `session/cancel`, `session/list`, `session/get`.
Notifications emitted during a turn (`textDelta`, `toolCall`,
`toolResult`, `stepStart`, `stepEnd`, `turnEnd`, `error`) carry the same
payloads as the SSE `data:` field.

ACP does not honor `OTTER_SERVER_TOKEN` — the trust model is "the
parent process spawned us, so the parent process is authorized."

## Operational notes

- One process holds one `Provider` for its lifetime; restart to swap.
- Every session is journaled to `~/.chimera/eventlog/otter-*` regardless
  of transport. Pass `"persist": false` in the `POST /sessions` body to
  skip (the server-side equivalent of `--no-save`).
- Sessions run in parallel; the server uses `asyncio` with one task per
  active turn, bounded by the `LoopConfig` cancellation token.
- No built-in rate limiting or TLS; front otter with Caddy / nginx in
  production.
- Structured logs go to stderr.

## See also

- [`quickstart.md`](quickstart.md) — first-call walkthrough including
  the server entry point.
- [`sessions.md`](sessions.md) — on-disk schema mirrored by the
  `/sessions` endpoints.
- [`share.md`](share.md) — `POST /sessions/{id}/share` and
  `GET /sessions/{id}/transcript` route to the same code as the CLI
  `share` command.
- [`providers.md`](providers.md) — provider chain that decides which
  SDK powers `model`.

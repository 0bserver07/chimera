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
- No built-in rate limiting; front otter with Caddy / nginx in
  production if you need throttling. Built-in TLS is available via
  `--tls-cert` / `--tls-key` (see below) for off-localhost deployments.
- Structured logs go to stderr.

## SSE resume contract

The `GET /session/<id>/events` handler honors the standard SSE
`Last-Event-ID` request header so a client whose connection drops mid-run
can reconnect and pick up where it left off without replaying every
already-seen frame.

### Wire shape

Every SSE record the server sends carries a numeric `id:` line:

```
id: 7
event: loop_event
data: {"message_id": "…", "type": "tool_call", "data": {…}, "turn": 0, "timestamp": 1714…}

```

`id` is a 1-based monotonic counter scoped to the session — it equals the
position of the envelope in the session's append-only event log.

### Reconnect protocol

On reconnect, the client supplies the last id it successfully observed:

```http
GET /session/<id>/events HTTP/1.1
Last-Event-ID: 7
```

The server replays only frames whose id is **strictly greater** than the
supplied cursor (id > 7), then continues to stream live frames as they
are emitted. Concretely:

| Header value          | Replay behavior                                           |
|-----------------------|-----------------------------------------------------------|
| Header absent         | Full history replay, then live frames.                    |
| `Last-Event-ID: 0`    | Full history replay (no frame has id ≤ 0).                |
| `Last-Event-ID: N`    | Skip every frame with id ≤ N; replay the rest; then live. |
| `Last-Event-ID: 99…`  | (Past current count) Replay nothing; deliver live frames. |
| Non-integer / blank   | Treated as absent — full replay (per the SSE spec).       |

### Client expectations

- Standard `EventSource` clients populate `Last-Event-ID` automatically
  on reconnect — no special handling needed in JavaScript.
- Custom HTTP clients (curl, Python `urllib`, Go's `http.Client`) must
  set the header explicitly; the server does not infer the cursor from a
  cookie or query string.
- The server never rewrites the cursor — id `N` always maps to the same
  envelope across the lifetime of the session.

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

## TLS (`--tls-cert` / `--tls-key`)

Once `chimera otter serve` binds anywhere other than `127.0.0.1`, the
bearer token in `Authorization: Bearer …` rides any on-path observer's
tcpdump. Pair the token with TLS so the channel is confidential before
the auth header even leaves the client.

```bash
chimera otter serve \
  --host 0.0.0.0 --port 5173 \
  --auth-token "$OTTER_TOKEN" \
  --tls-cert /etc/otter/server.crt \
  --tls-key /etc/otter/server.key
```

When both flags are set the server wraps its listening socket via
[`ssl.SSLContext.wrap_socket`](https://docs.python.org/3/library/ssl.html#ssl.SSLContext.wrap_socket)
with `PROTOCOL_TLS_SERVER` and `load_cert_chain(certfile, keyfile)` —
stdlib only, no extra runtime dependency. Cleartext clients that try
to reach the same port get a connection error instead of an HTTP
response, which is the correct failure mode for a TLS endpoint.

Operational rules:

- Both flags must be supplied together. Passing only one is a usage
  error (`exit 2`) before the socket is bound.
- `--auth-token` is **strongly recommended** alongside TLS. TLS hides
  the bearer token in transit; the token is still what proves the
  caller is allowed to drive the agent.
- For LAN / staging use, a self-signed cert generated with `openssl
  req -x509 …` (or any other CA toolchain) is sufficient. Production
  deployments should use a cert from your real CA.
- Certificate rotation is not hot-swappable today: restart the server
  to pick up a new cert chain.
- The startup banner on stderr switches from `http://…` to `https://…`
  so logs unambiguously reflect the active scheme.

Browser clients connecting over HTTPS still face the `EventSource`
limitation noted in the auth section — terminate TLS at a reverse
proxy if you need it to inject the `Authorization` header for an
in-browser SSE consumer.

## Custom slash commands over HTTP

Wave-3 (F4) lifts the otter REPL's `.opencode/command/*.md` palette onto
the HTTP surface so a TUI / IDE / web client gets parity with the
in-process slash dispatcher. Two routes:

### `GET /commands`

List every custom slash command discovered under the server's
commands cwd (`commands_cwd` constructor arg, defaulting to
`os.getcwd()` resolved per-call). Project scope (`<cwd>/.opencode/command/*.md`)
overrides user scope (`~/.opencode/command/*.md`) on name conflicts —
matching the upstream's last-wins precedence ladder used by the REPL.

Response shape:

```json
{
  "commands": [
    {
      "name": "summarize",
      "description": "Summarize $1 about $TARGET",
      "args": [
        {"name": "target", "description": "subject of the summary"}
      ],
      "source": "/abs/path/.opencode/command/summarize.md"
    }
  ]
}
```

Empty palette returns `200 OK` with `{"commands": []}` (not 404), so
client UIs that pre-populate a command picker on startup can render an
empty palette without special-casing the missing-directory branch.

### `POST /commands/<name>/invoke`

Render a custom command template and push the rendered prompt as a
new user turn into an existing session — the same code path
`POST /session/<id>/message` exercises, including SSE fan-out.

Body:

```json
{
  "session_id": "abc123",
  "args": ["chapter-7"],
  "kwargs": {"target": "the otter REPL"}
}
```

| Field         | Required | Notes                                                     |
|---------------|----------|-----------------------------------------------------------|
| `session_id`  | yes      | Existing session id from `POST /session`.                 |
| `args`        | no       | Positional args. Map to `$1`, `$2`, … in the template.    |
| `kwargs`      | no       | Named args. Map to `$ARG_NAME` (case-insensitive).        |

Response (`202 Accepted`):

```json
{
  "message_id": "…",
  "name": "summarize",
  "rendered": "Please summarize chapter-7 — focus on the otter REPL."
}
```

The rendered prompt is forwarded to `submit_message`, so SSE clients on
`GET /session/<id>/events` see `user_message` followed by the same
`loop_event` / `result` stream a direct prompt would have produced. The
HTTP route is the network-level mirror of
`chimera.otter.slash.build_custom_command_handler` — same precedence
ladder, same render semantics, same drop-into-the-session behavior.

| Status | Body                                                | Cause                                |
|--------|-----------------------------------------------------|--------------------------------------|
| 202    | `{message_id, name, rendered}`                      | Render + submit succeeded.           |
| 400    | `{"error": "missing_session_id"}`                   | Body lacks `session_id`.             |
| 400    | `{"error": "args_must_be_list"}`                    | `args` is not a JSON list.           |
| 400    | `{"error": "kwargs_must_be_object"}`                | `kwargs` is not a JSON object.       |
| 404    | `{"error": "session_not_found"}`                    | Unknown `session_id`.                |
| 404    | `{"error": "command_not_found", "name": "<name>"}`  | No `.md` file matches `<name>`.      |
| 500    | `{"error": "command_invoke_failed", "detail": …}`   | Renderer or submit raised.           |

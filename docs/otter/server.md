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

## Cost rollups across persisted runs

Wave-4 (L6) lifts the M4 `chimera mink runs cost` aggregation onto the
HTTP surface so a TUI / IDE / web client can pull the same rollups
without shelling out to the CLI. Both routes walk
`~/.chimera/eventlog/mink-*` **and** `~/.chimera/eventlog/otter-*` so
the two persistence corpora are reported together. Bearer auth applies
identically to every other endpoint (`OTTER_SERVER_TOKEN`).

The eventlog root is taken from
`chimera.mink.runs.default_eventlog_root()` per request so the routes
always reflect the live filesystem; tests inject a `tmp_path` via the
`OtterServer(eventlog_root=...)` constructor argument.

### `GET /runs`

Lightweight list of run summaries. One row per persisted run, newest
first.

Query parameters:

| Param   | Notes                                                                  |
|---------|------------------------------------------------------------------------|
| `since` | `7d` / `24h` / `30m` shorthand or any ISO-8601 date / datetime.        |
| `model` | Case-insensitive substring filter on the model name. `all` = no filter.|
| `limit` | Cap row count (newest first). Non-integer values return `400`.         |

Response shape:

```json
{
  "total_runs": 4,
  "runs": [
    {
      "run_id": "otter-20260425T120300-aaaa1111",
      "started_at": "2026-04-25T12:03:00Z",
      "ended_at": "2026-04-25T12:03:30Z",
      "model": "glm-5.1:cloud",
      "prompt": "do the thing",
      "success": true,
      "cost_usd": 0.07,
      "steps": 4,
      "tool_calls": 3,
      "source": "otter"
    }
  ]
}
```

`source` is `"mink"` or `"otter"` so the client can render the two
corpora distinctly without re-parsing the run id.

### `GET /runs/cost`

Cost rollup for the same corpus. Same query parameters as `/runs`. The
response carries both the flat top-level shape promised in the task
contract and a strict-superset `totals` block that mirrors `chimera
mink runs cost --format json` for clients already integrated against
the CLI.

```json
{
  "total_runs": 4,
  "total_cost": 0.22,
  "total_tokens": 3800,
  "by_model": {
    "glm-5.1:cloud":     {"runs": 2, "cost_usd": 0.10, "tokens": 1800},
    "claude-sonnet-4-6": {"runs": 2, "cost_usd": 0.12, "tokens": 2000}
  },
  "by_run": [
    {
      "run_id": "otter-20260425T120300-aaaa1111",
      "started_at": "2026-04-25T12:03:00Z",
      "model": "glm-5.1:cloud",
      "cost_usd": 0.07,
      "total_tokens": 1200,
      "input_tokens": 800,
      "output_tokens": 350,
      "cache_tokens": 50,
      "success": true,
      "steps": 4,
      "source": "otter"
    }
  ],
  "totals": {
    "runs": 4,
    "successful_runs": 3,
    "failed_runs": 1,
    "cost_usd": 0.22,
    "tokens": 3800,
    "input_tokens": 800,
    "output_tokens": 350,
    "cache_tokens": 50,
    "avg_cost_usd": 0.055,
    "p50_cost_usd": 0.05,
    "p95_cost_usd": 0.12
  },
  "filters": {"since": null, "model": null}
}
```

Worked example:

```bash
curl -s -H "Authorization: Bearer $OTTER_SERVER_TOKEN" \
  "http://127.0.0.1:5173/runs/cost?since=7d&model=glm-5.1:cloud&limit=50" \
  | jq '{runs: .total_runs, cost: .total_cost, by_model}'
```

| Status | Body                                              | Cause                                |
|--------|---------------------------------------------------|--------------------------------------|
| 200    | `{total_runs, total_cost, total_tokens, …}`       | Aggregation succeeded.               |
| 400    | `{"error": "invalid_query", "detail": …}`         | Malformed `since` / `limit`.         |
| 401    | `{"error": "unauthorized"}`                       | Missing or wrong bearer token.       |
| 500    | `{"error": "runs_cost_failed", "detail": …}`      | Filesystem or aggregator raised.     |

## Multi-session support

`chimera otter serve` is **multi-session out of the box**. A single
server process owns many concurrent `OtterSessionState` objects in
parallel — a TUI client, an IDE plugin, an evals harness, and a web UI
can all drive the same server simultaneously without contending on a
global lock.

Sessions are owned by an `OtterSessionManager` (a thin layer over a
`dict[str, OtterSessionState]` plus a `threading.Lock`). The manager's
lock guards *only* the dict; agent runs hold no manager-wide lock, so
two sessions running ReAct loops in parallel never wait on each other.

### TTL eviction

Idle sessions are reaped after `OtterSessionManager.ttl` seconds (one
hour by default — `DEFAULT_SESSION_TTL`). Every observable activity
bumps the session's `last_touched` timestamp:

* `POST /session` — create
* `GET /session/<id>` — state snapshot
* `POST /session/<id>/message` — agent dispatch
* `POST /session/<id>/cancel` — cooperative cancel
* `GET /session/<id>/events` — SSE subscribe (full + reconnect replay)
* every `emit_event` fan-out (server-driven activity)

Eviction is opportunistic: every public mutation on the manager calls
`evict_idle()` first, so callers don't need a background sweeper. When
a session is evicted, its SSE subscribers receive the `None` sentinel
(generators exit cleanly), pending permission gates are released, and
its cancellation token is flipped so any in-flight agent thread halts
on its next yield.

To disable TTL eviction (interactive REPL clients that may sit idle
overnight), pass `session_ttl=None` when instantiating the server, or
inject a manager built with `OtterSessionManager(ttl=None)`.

### `GET /sessions`

Multi-session listing — returns metadata for every active session,
newest-touched first:

```json
{
  "sessions": [
    {
      "session_id": "9c1...",
      "working_dir": "/path/to/project",
      "created_at": 1745000000.0,
      "last_touched": 1745000123.0,
      "event_count": 42
    }
  ]
}
```

```bash
curl -s -H "Authorization: Bearer $OTTER_SERVER_TOKEN" \
  http://127.0.0.1:5173/sessions \
  | jq '.sessions[] | {id: .session_id, idle: (now - .last_touched)}'
```

`GET /session` (singular) still returns the bare-id list for
back-compat with existing clients.

### `DELETE /session/<id>`

Explicit teardown. Returns `204 No Content` on hit, `404` on miss.
Wakes SSE subscribers, releases pending permission gates, cancels any
in-flight agent run, and removes the session from the manager:

```bash
curl -s -X DELETE \
  -H "Authorization: Bearer $OTTER_SERVER_TOKEN" \
  http://127.0.0.1:5173/session/9c1...
```

| Status | Body                                | Cause                       |
|--------|-------------------------------------|-----------------------------|
| 204    | (empty)                             | Session torn down.          |
| 404    | `{"error": "session_not_found"}`    | Unknown session id.         |
| 401    | `{"error": "unauthorized"}`         | Missing/wrong bearer token. |

### Per-session SSE replay buffer

Every session owns an independent `state.events` list — the SSE replay
buffer for `GET /session/<id>/events`. Two concurrent sessions
therefore have entirely disjoint event histories: a `Last-Event-ID`
reconnect on session A only ever replays A's frames, and `emit_event`
on B never reaches A's subscribers. This is asserted end-to-end by
`tests/otter/test_server_multi_session.py`.

### Configuring multi-session behaviour

`OtterServer` accepts:

* `session_manager: OtterSessionManager | None` — inject a shared
  manager (handy in tests with a deterministic clock, or to share a
  manager across an HTTP and ACP front-end on the same process).
* `session_ttl: float | None` — TTL for the auto-built manager when
  `session_manager` is `None`. Defaults to `DEFAULT_SESSION_TTL`
  (3600s). `None` or `0` disables eviction.

```python
from chimera.otter.server import OtterServer, OtterSessionManager

# Custom 10-minute idle TTL.
srv = OtterServer(agent_factory=..., session_ttl=600.0)

# Or inject a manager directly.
mgr = OtterSessionManager(ttl=600.0)
srv = OtterServer(agent_factory=..., session_manager=mgr)
```

## Managing backgrounded servers (`serve status` / `serve stop`)

When `chimera otter serve` is launched in the background (e.g. `&` in a
shell, a `tmux` pane, or a launchd job), the running PID, port, and a
SHA-256 of the auth token are recorded in
`~/.chimera/run/otter-<port>.pid`. Two subcommands consume that on-disk
record so a separate shell can list and graceful-stop those servers
without hand-rolling `ps` / `lsof` parsing.

### `chimera otter serve status`

Lists every backgrounded otter server discovered under
`~/.chimera/run/`. One line per pidfile:

```text
otter port=5173 pid=12345 alive=yes scheme=https auth=yes /Users/you/.chimera/run/otter-5173.pid
otter port=5183 pid=88888 alive=no (stale) scheme=http auth=no /Users/you/.chimera/run/otter-5183.pid
```

`alive=no (stale)` flags a pidfile whose process has exited without a
clean shutdown — `serve stop` will reap it idempotently.

### `chimera otter serve stop [--port N | --all] [--serve-timeout N]`

Gracefully terminates one or every running otter server.

- **No arguments**: if exactly one otter pidfile exists, stop it. If
  more than one is running, exit 2 with a "disambiguate" error.
- **`--port N`**: target only the matching `otter-<N>.pid` record.
- **`--all`**: stop every backgrounded otter server.
- **`--serve-timeout N`**: seconds to wait between SIGTERM and the
  SIGKILL escalation. Default `10.0`.

The shutdown sequence is **graceful first**, per the project rule
(`CLAUDE.md`): `SIGTERM` → wait up to `--serve-timeout` seconds → only
escalate to `SIGKILL` when the process is still alive after the wait.
`SIGKILL` is never the first signal sent.

Exit codes: `0` on every targeted process stopping (or no pidfiles to
match — idempotent), `1` when at least one process refused both signals,
`2` on a usage error (`stop` with multiple servers and no
`--port` / `--all`).

### Pidfile schema

```json
{
  "pid": 12345,
  "host": "127.0.0.1",
  "port": 5173,
  "prefix": "otter",
  "auth_token_hash": "sha256:9c…",
  "started_at": 1714500000.0,
  "scheme": "https"
}
```

`auth_token_hash` is `null` when the server runs without `--auth-token`.
Storing only the SHA-256 keeps the bearer secret off disk while still
letting future tooling assert the caller knows the token.

### Library API

The same primitives are exported under `chimera.otter.server_pidfile`
for embedders that drive the server programmatically:

```python
from chimera.otter import server_pidfile

# List every running server.
records = server_pidfile.list_pidfiles(prefix="otter")

# Stop the otter server on port 5173 with a 5-second SIGTERM window.
server_pidfile.stop_all(prefix="otter", port=5173, timeout=5.0)
```

Pidfile management is opt-in: `OtterServer(pidfile_prefix="otter")` (or
`serve_http(pidfile_prefix="otter")`) writes the record on bind and
removes it on graceful shutdown. With `pidfile_prefix=None` (the
default) no pidfile is touched, which is what you want for in-process
test harnesses and library embedders.

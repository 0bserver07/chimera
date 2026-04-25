# `chimera otter` ACP server

The Agent Client Protocol (ACP) is a JSON-RPC 2.0 wire format that
lets external IDE / TUI clients drive an agent session over stdio
without bespoke plumbing. Otter ships an ACP **server** transport so
clients like Zed (and any other ACP-aware IDE) can drive a Chimera
session through the same surface they'd use against the upstream
open-source coding agent.

This is the inverse of `chimera/acp/client.py`: where the client
*spawns* an external ACP-speaking agent and drives it as a subprocess,
`chimera/otter/acp.py` *is* the agent — it accepts requests on stdin
and emits responses + `session/update` notifications on stdout.

## Launching

```sh
# Start an ACP server on stdio.
chimera otter serve --acp

# Or pass directly via stdin/stdout to another tool.
some-ide | chimera otter serve --acp | some-ide
```

The server takes the standard otter flags (`--cwd`, `--model`,
`--allowed-tools`) and runs until the input stream closes.

## Wire protocol

- **Transport.** Newline-delimited JSON-RPC 2.0. One JSON object per
  line, no Content-Length headers, no SSE framing.
- **Requests** carry an integer `id`; the server responds with the
  same `id`.
- **Notifications** (server → client) have no `id` and use the
  `method` `session/update`.

Example request → response round-trip:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"0.1","capabilities":{}}}
```

## Methods

The server implements the core ACP method set:

| Method                      | Purpose                                                   |
|-----------------------------|-----------------------------------------------------------|
| `initialize`                | Capability handshake.                                     |
| `session/new`               | Create a new session; returns `sessionId`.                |
| `session/load`              | Load a previously persisted session by id.                |
| `session/list`              | List known sessions.                                      |
| `session/message`           | Send a user message; emits `session/update` events; replies with the final assistant message. |
| `session/cancel`            | Cancel an in-flight turn.                                 |
| `session/setMode`           | Switch the active agent (`build` / `plan` / etc.).        |
| `session/setModel`          | Switch the active model.                                  |
| `permission/respond`        | Reply to a pending permission request (`allow` / `deny` / `always`). |

Unknown methods return JSON-RPC error code `-32601` (Method not found).

## Notifications

The server pushes `session/update` notifications on the same stream
during a turn. The shape mirrors the upstream agent:

```json
{
  "jsonrpc":"2.0",
  "method":"session/update",
  "params":{
    "sessionId":"01J9...",
    "update":{
      "sessionUpdate":"agent_message_chunk",
      "content":[{"type":"text","text":"..."}]
    }
  }
}
```

Five `sessionUpdate` kinds are emitted:

- `agent_message_chunk` — streaming text from the assistant.
- `tool_call` — a tool was invoked (with `tool` name, `input`, and
  `callId`).
- `tool_result` — the tool returned (success or error, with
  `callId` correlation).
- `usage_update` — token counts and cost so far.
- `permission_request` — the agent asked for permission to run a
  risky tool. The client replies via `permission/respond`.

## Permission round-trip

When a tool call requires user confirmation (per the active permission
ruleset), the server emits a `permission_request` notification:

```json
{"sessionUpdate":"permission_request","requestId":"r1","tool":"bash","input":{"cmd":"rm -rf /tmp/x"},"options":["allow","always","deny"]}
```

The client replies with:

```json
{"jsonrpc":"2.0","id":42,"method":"permission/respond","params":{"requestId":"r1","reply":"allow"}}
```

The server resolves the pending future and the turn continues. The
client is responsible for surfacing the prompt to the user; otter
does not assume any particular UI.

## Session state

Each session is backed by a Chimera `Session` instance with its own
agent loop, message queue, and cancellation token. Sessions persist
across `session/load` calls via `~/.chimera/eventlog/otter-<id>/` (the
same directory layout used by `chimera otter sessions`).

State machine per session:

- **idle** — no in-flight turn; ready for `session/message`.
- **running** — a turn is in progress; emitting `session/update`.
- **cancelled** — `session/cancel` arrived; the turn is unwinding.

A second `session/message` arriving while the session is **running**
queues behind the active turn (mirrors the upstream behavior).

## Error handling

JSON-RPC error codes used by the server:

| Code     | Meaning                          |
|----------|----------------------------------|
| `-32700` | Parse error (malformed JSON)     |
| `-32600` | Invalid request                  |
| `-32601` | Method not found                 |
| `-32602` | Invalid params                   |
| `-32603` | Internal error                   |
| `-32000` | Application-level error (with `data` payload describing the agent error) |

The agent never crashes the server on internal errors; they're caught
and surfaced as `-32000` responses with a structured `data` payload
(`{name, message, stack}`).

## Cancellation

`session/cancel` flips the session's `CancellationToken`, which
unwinds the current tool call cooperatively (per
`chimera/core/cancellation.py`). The server replies to the in-flight
`session/message` request with a final `agent_message_chunk` carrying
the partial assistant output, plus a marker indicating the turn was
cancelled.

## Capabilities handshake

`initialize` advertises the server's capabilities so the client can
adapt:

```json
{
  "protocolVersion":"0.1",
  "serverInfo":{"name":"otter-acp","version":"0.1.0"},
  "capabilities":{
    "sessionFork":true,
    "sessionList":true,
    "permissionRequest":true,
    "thinking":false,
    "vision":false
  }
}
```

The version string follows the upstream ACP spec; clients should
validate the major version and gracefully handle minor differences.

## Pure mode

Otter does not currently honor the upstream `--pure` flag (no
external plugins). To get the same effect, narrow the tool surface
with `--allowed-tools`:

```sh
chimera otter serve --acp --allowed-tools Read,Grep,Bash
```

## Test surface

`tests/otter/test_acp.py` covers:

- The full `initialize` → `session/new` → `session/message` →
  `session/cancel` flow against an in-process server.
- Permission round-trip: pending request blocks the turn until
  `permission/respond` arrives.
- Unknown method returns `-32601`.
- Malformed JSON returns `-32700` and the server keeps the stream
  alive.
- Concurrent `session/message` calls queue per session.

## See also

- `chimera/otter/acp.py` — implementation.
- `chimera/acp/client.py` — the inverse: ACP client that drives an
  external agent.
- `docs/otter/parity-matrix.md` — overall parity status.
- The Agent Client Protocol reference (search for "agent client
  protocol" in your IDE's docs; the spec is published by the upstream
  ecosystem and is implementation-language-neutral).

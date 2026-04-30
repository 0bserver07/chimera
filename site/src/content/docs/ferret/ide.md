---
title: Ferret IDE Bridge
description: IDE-first ACP schema for chimera ferret — notification kinds, capability handshake, and integration recipes for Zed and VS Code.
---

# IDE bridge — driving ferret from an editor

`chimera ferret serve` defaults to an **ACP** (Agent Client Protocol)
JSON-RPC server over stdio. This is the IDE-first stance: the
upstream IDE-first OpenAI-flagship coding agent is overwhelmingly
driven from an editor plugin, and ferret follows suit. HTTP is
opt-in via `--http`.

This page covers ferret's ACP schema (which is a strict superset of
otter's ACP server documented in [`../otter/acp.md`](../otter/acp.md))
and gives a worked integration recipe for Zed and VS Code.

## Launching

```bash
# ACP over stdio (the default)
chimera ferret serve

# HTTP server, opt-in
chimera ferret serve --http --port 5173
```

The server takes the standard ferret flags (`--cwd`, `--model`,
`--sandbox`, `--approval`, `--allowed-tools`) and runs until the
input stream closes.

```bash
chimera ferret serve --sandbox workspace-write --approval auto
```

## Wire protocol

- **Transport:** newline-delimited JSON-RPC 2.0. One JSON object per
  line, no `Content-Length` headers, no SSE framing.
- **Requests** carry an integer `id`; the server responds with the
  same `id`.
- **Notifications** (server → client) have no `id` and use the
  method name `session/update` with a structured `kind` field.

Example handshake:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"0.2","capabilities":{"sandbox":true,"approval":true,"streaming":true,"cancellation":true,"bridge":true}}}
```

The capability block is the IDE-first piece: ferret advertises
`sandbox`, `approval`, and `bridge` so editor plugins can render
sandbox/approval pickers and expose the cloud bridge as a setting.

## Methods

The server implements the core ACP method set plus four
ferret-specific extensions.

| Method                 | Purpose |
|------------------------|---------|
| `initialize`           | Capability handshake. |
| `session/new`          | Create a new session; returns `sessionId`. |
| `session/load`         | Load a previously persisted session by id. |
| `session/list`         | List known sessions under `~/.chimera/eventlog/ferret-*/`. |
| `session/message`      | Send a user message; emits `session/update` events; replies with the final assistant message. |
| `session/cancel`       | Cancel an in-flight turn. |
| `session/setMode`      | Switch the active agent. |
| `session/setModel`     | Switch the active model. |
| `session/setSandbox`   | **Ferret extension.** Reads the active sandbox mode (write returns "restart required"). |
| `session/setApproval`  | **Ferret extension.** Switch the approval preset live. |
| `session/bridgeStatus` | **Ferret extension.** Returns cloud-bridge state if attached. |
| `permission/respond`   | Reply to a pending permission request (`allow` / `deny` / `always`). |

Unknown methods return JSON-RPC error code `-32601` (Method not
found).

## Notification kinds

The `session/update` notification carries a `kind` that tells the
IDE plugin how to render the payload. Ferret emits the standard ACP
kinds plus four IDE-first extensions.

| `kind` | Direction | Payload | Render hint |
|---|---|---|---|
| `agent_message_chunk` | server → client | `{"text": "..."}` | Append to streaming assistant bubble. |
| `tool_call_start` | server → client | `{"id", "tool", "args"}` | New tool-call card. |
| `tool_call_progress` | server → client | `{"id", "stdout"/"stderr"}` | Append to tool-call card body. |
| `tool_call_end` | server → client | `{"id", "result", "duration_ms"}` | Close tool-call card. |
| `permission_request` | server → client | `{"id", "tool", "args", "risk"}` | Modal: allow / deny / always. |
| `sandbox_violation` | server → client | `{"tool", "path", "mode"}` | Inline error banner. |
| `approval_changed` | server → client | `{"old", "new"}` | Status-bar pill update. |
| `cost_update` | server → client | `{"input", "output", "cache", "reasoning", "total_usd"}` | Status-bar cost. |
| `file_edit` | server → client | `{"path", "diff"}` | Editor in-place diff with apply/revert. |
| `file_open` | server → client | `{"path", "line"?}` | Editor "reveal in editor" hint. |
| `notice` | server → client | `{"level", "text"}` | Toast notification. |
| `turn_end` | server → client | `{"id", "stop_reason"}` | Turn complete; accept input. |

The four IDE-first extensions are `sandbox_violation`,
`approval_changed`, `file_edit` (with structured diff for in-place
apply), and `file_open` (for "agent says: look at this line").
Plugins that don't recognize these MAY ignore them; the standard
`agent_message_chunk` carries enough text for a fallback.

## Permission round-trip

When a tool call needs user confirmation (e.g., approval preset
`auto` is active and the bash classifier flags an `ask` rule):

```json
// server -> client
{"jsonrpc":"2.0","method":"session/update","params":{
  "sessionId":"...","kind":"permission_request",
  "id":"perm-7","tool":"Bash",
  "args":{"command":"git push origin main"},"risk":"medium"}}

// client -> server (when the user clicks "Allow once")
{"jsonrpc":"2.0","id":42,"method":"permission/respond",
 "params":{"sessionId":"...","id":"perm-7","decision":"allow"}}

// server -> client (response to the request id)
{"jsonrpc":"2.0","id":42,"result":{"ok":true}}
```

`decision` is one of `allow` | `deny` | `always` | `never`. The
last two persist into the audit log and bias future decisions for
the same `(tool, normalized_args)` key.

## Integration recipe — Zed

Zed already speaks ACP natively. To register ferret as the agent:

1. Open `~/.config/zed/settings.json`.
2. Add an `agent_servers` entry:
   ```json
   {
     "agent_servers": {
       "ferret": {
         "command": "chimera",
         "args": ["ferret", "serve", "--sandbox", "workspace-write", "--approval", "auto"]
       }
     }
   }
   ```
3. Open the Zed agent panel and pick `ferret` from the agent
   dropdown.

Zed's plugin recognizes the `tool_call_start` / `tool_call_end` /
`agent_message_chunk` notification kinds. The ferret-specific
`file_edit` notification is rendered as an inline diff with
apply/revert affordances.

## Integration recipe — VS Code

VS Code does not ship a built-in ACP client, so the recipe is to use
the Chimera VS Code extension (or any community ACP plugin) and
point it at `chimera ferret serve`:

1. Install the extension from the marketplace.
2. Configure `chimera.command` to `chimera`.
3. Configure `chimera.args` to
   `["ferret", "serve", "--sandbox", "workspace-write", "--approval", "auto"]`.
4. Open the Command Palette → "Chimera: Start Session".

The extension translates ACP notifications into VS Code UI:

- `agent_message_chunk` → chat panel streaming text.
- `tool_call_start` / `tool_call_end` → tool-call cards.
- `permission_request` → quick-pick prompt.
- `file_edit` → `WorkspaceEdit` with a code lens to accept / reject.
- `file_open` → `vscode.window.showTextDocument(uri, line)`.

## HTTP server (opt-in)

When you want a non-IDE client (an evals harness, a CI bot, a
bespoke web UI) to drive ferret, use HTTP instead:

```bash
chimera ferret serve --http --port 5173
```

The HTTP surface mirrors `chimera otter serve` — see
[`../otter/server.md`](../otter/server.md) for endpoint shapes, SSE
event format, and `FERRET_SERVER_TOKEN` Bearer auth. The same
session is reachable from both transports simultaneously, so an IDE
plugin and a CI bot can attach to one ferret process at the same
time.

## Cloud bridge

For driving ferret from a remote UI (a web dashboard, a phone, a
remote ops console), use the optional cloud bridge instead of
exposing the HTTP server publicly. See
[`cloud-bridge.md`](cloud-bridge.md).

## See also

- [`quickstart.md`](quickstart.md) — first-run walk-through.
- [`cloud-bridge.md`](cloud-bridge.md) — remote-UI bridge.
- [`../otter/acp.md`](../otter/acp.md) — sibling ACP server (no
  ferret-specific extensions).
- [`../otter/server.md`](../otter/server.md) — HTTP surface shared
  by `--http` mode.

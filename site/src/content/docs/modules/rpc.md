---
title: "RPC"
description: "RPC"
---

`chimera.rpc` exposes a headless JSON-lines RPC interface so external
processes (editors, GUIs, scripts) can drive an agent session over stdin/stdout
without embedding Python.

## Starting the server

```sh
chimera code --mode rpc
```

The process reads newline-delimited JSON from stdin and writes responses and
events to stdout.

## Commands (inbound)

| Type | Extra fields | Description |
|------|-------------|-------------|
| `prompt` | `message: str` | Send a user message; triggers a full agent turn |
| `steer` | `message: str` | Inject a mid-turn steering message |
| `cancel` | — | Cancel the in-progress turn |
| `get_state` | — | Request current messages and model name |
| `compact` | — | Trigger context compaction |
| `set_model` | `model: str` | Switch the active model |

Every command carries `type` and an optional `id` for correlation.

## Responses and events (outbound)

| Type | Key fields | Description |
|------|-----------|-------------|
| `RpcResponse` | `command`, `id`, `success`, `error` | Acknowledgement for any command |
| `StateResponse` | `id`, `messages`, `model` | Reply to `get_state` |
| `MessageEvent` | `role`, `content`, `done` | Streamed assistant output |
| `ToolExecutionEvent` | `tool_name`, `tool_args`, `status`, `result` | Tool call lifecycle |
| `ErrorEvent` | `message` | Unhandled error notification |

## JSON exchange example

```json
// → stdin
{"type": "prompt", "id": "r1", "message": "List files in src/"}

// ← stdout (event, then response)
{"type": "message", "role": "assistant", "content": "auth.py  main.py", "done": true}
{"command": "prompt", "id": "r1", "success": true, "error": ""}
```

## RpcServer and RpcHandler

`RpcServer` parses stdin and dispatches to registered handlers.
`RpcHandler` provides the five concrete handler methods and exposes them via
the `handlers` property.

```python
from chimera.rpc.server import RpcServer
from chimera.rpc.handler import RpcHandler

server = RpcServer(session=my_session)
handler = RpcHandler(server)
server.set_handlers(handler.handlers)
server.run()  # blocks until EOF on stdin
```

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/rpc/` -- verified against current source at wave-17.

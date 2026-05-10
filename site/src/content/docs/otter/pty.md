---
title: chimera otter serve — PTY routes
description: When otter is running as a server (chimera otter serve), it exposes a small set of HTTP routes that spawn long-running interactive subprocesses against a real pseudo-terminal so TTY-aware tools (vim, python -i, npm run dev, full-screen REPL
---

# `chimera otter serve` — PTY routes

When otter is running as a server (`chimera otter serve`), it exposes a
small set of HTTP routes that spawn long-running interactive
subprocesses against a real pseudo-terminal so TTY-aware tools (`vim`,
`python -i`, `npm run dev`, full-screen REPLs) stream both ways. This
augments the per-session SSE event stream with a parallel raw byte
channel.

## Routes

| Path | Method | Purpose |
|------|--------|---------|
| `/session/<id>/pty/start` | `POST` | Spawn a PTY-backed subprocess. Body: `{"command": "...", "cols": 80, "rows": 24, "env": {...}, "cwd": "..."}`. Returns `{"pty_id": "..."}`. |
| `/session/<id>/pty/<pty_id>/input` | `POST` | Write to stdin. Body: `{"data": "..."}`. Returns `{"written": N}`. |
| `/session/<id>/pty/<pty_id>/output` | `GET` | Drain pending stdout. Returns `{"data": "...", "exit": null \| <int>}`. |
| `/session/<id>/pty/<pty_id>/resize` | `POST` | Resize the PTY. Body: `{"cols": 120, "rows": 40}`. |
| `/session/<id>/pty/<pty_id>/stop` | `POST` | SIGTERM the child. Returns `{"exit_code": ...}`. |
| `/session/<id>/pty/stream?pty_id=<id>` | `GET` | SSE stream of `{"data": "..."}` chunks until EOF. |

All routes inherit the auth model documented in
[`docs/otter/server.md`](server.md): the master `--auth-token` and any
per-session bearer token both authorize the PTY routes for the matching
session.

## Example

```bash
chimera otter serve --port 5173 &
TOKEN_=$(...)
SESS_=$(curl -s -X POST localhost:5173/session -d '{"working_dir":"/tmp"}' | jq -r .session_id)

# Spawn an interactive python -i.
PTY_=$(curl -s -X POST localhost:5173/session/$SESS_/pty/start \
  -d '{"command": "python -i", "cols":120, "rows":40}' | jq -r .pty_id)

# Send a line, drain output.
curl -s -X POST localhost:5173/session/$SESS_/pty/$PTY_/input \
  -d '{"data": "print(2+2)\n"}'
curl -s localhost:5173/session/$SESS_/pty/$PTY_/output | jq .data

# Stream stdout via SSE.
curl -s localhost:5173/session/$SESS_/pty/stream?pty_id=$PTY_

# Tear it down.
curl -s -X POST localhost:5173/session/$SESS_/pty/$PTY_/stop
```

## Implementation notes

* **Stdlib only.** `chimera/otter/pty.py` uses `pty.openpty` plus
  `subprocess.Popen` with the slave fd hooked up to `stdin`/`stdout`/
  `stderr`. A daemon reader thread drains the master fd into a per-PTY
  buffer and onto every SSE subscriber queue.
* **POSIX only.** The `pty` module is POSIX-only; on Windows the
  manager raises a `RuntimeError` at `start` time and the route
  returns 500 with `{"error": "pty_start_failed"}`.
* **Reaping.** `OtterServer.shutdown()` calls
  `pty_manager.shutdown_all()` so every running PTY is sent SIGTERM
  before the server stops.
* **Trademark hygiene.** No upstream brand is named in source or docs;
  the trademark scrub covers `chimera/otter/pty.py` and this file.

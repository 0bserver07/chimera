---
title: Otter TUI (prototype)
description: Richer textual-based frontend for the otter REPL.
---

# Otter TUI (prototype)

`chimera otter` ships a readline-based REPL by default. The `--tui`
flag opens an alternative, richer frontend built on
[textual](https://textualize.io/). It is **prototype-grade**: layout,
key bindings, and event rendering may evolve before promotion.

## Install

The TUI requires the optional `[tui]` extra:

```sh
pip install 'chimera-run[tui]'
# or, with uv:
uv sync --extra tui
```

Without the extra, `chimera otter` still works — the default readline
REPL has no extra dependencies.

## Launch

```sh
chimera otter --tui
```

The TUI:

- spawns an in-process [`OtterServer`](server.md) (no socket bound),
- creates a fresh session in your current working directory,
- subscribes to the same Server-Sent Events stream a remote client
  would use, and
- routes user input through the standard `POST /session/<id>/message`
  surface.

That means improvements to the otter HTTP server (new SSE event
types, permission gating, cancel semantics) automatically flow into
the TUI.

## Key bindings

| Key      | Action                                                                |
|----------|-----------------------------------------------------------------------|
| `Ctrl+C` | Cancel the in-flight turn (calls `POST /session/<id>/cancel`).        |
| `Ctrl+D` | Quit the app.                                                         |
| `F1`     | Show / hide a help banner.                                            |
| `F2`     | Show / hide the right-side tool-call panel.                           |
| `Enter`  | Submit the current input as a new user message.                       |

## Layout

```
+------------------------------------------------------------+
| model: claude-sonnet-4-6   session: ab12cd  cost: $0.0042  |
+-----------------------------------------+------------------+
|                                         |                  |
|  user: hello                            |  tool calls:     |
|  agent: hi! what can I do for you?      |   - bash (ok)    |
|  ...                                    |   - read_file    |
|  (Conversation, RichLog)                |  (Side panel)    |
|                                         |                  |
+-----------------------------------------+------------------+
| > _                                                        |
+------------------------------------------------------------+
```

Top: status bar (model, session id prefix, cumulative cost, tool
call count, error count).
Center-left: scrollable conversation log.
Center-right: tool-call / event side panel (toggle with `F2`).
Bottom: input widget.

## Flags

The TUI honors the same scoping flags the readline REPL does:

- `--cwd PATH` — workspace root the agent operates in.
- `--model MODEL` — model identifier (resolved through the otter
  provider chain).
- `--no-lsp`, `--no-rules`, `--no-mcp`, `--no-plugins` — same opt-out
  semantics as `chimera otter -p PROMPT`.

## Programmatic use

The TUI can be embedded in tests or scripts via
[`chimera.otter.tui.build_app`](../../chimera/otter/tui.py):

```python
from chimera.otter.server import OtterServer
from chimera.otter.tui import TUIConfig, build_app

server = OtterServer(agent_factory=my_factory)
app = build_app(server, TUIConfig(model="glm-5"))
app.run()  # blocks; or use ``await app.run_async()`` from asyncio.
```

For tests, use textual's `App.run_test()` harness:

```python
async with app.run_test() as pilot:
    await pilot.press("h", "i", "enter")
    await pilot.pause()
```

## Status

- ![status: prototype](https://img.shields.io/badge/status-prototype-orange)
- Default REPL stays readline-based; TUI is opt-in via `--tui`.
- See `tests/otter/test_tui.py` for runnable usage examples.

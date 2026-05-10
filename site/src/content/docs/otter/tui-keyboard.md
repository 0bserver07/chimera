---
title: Otter TUI keyboard reference
description: Every keystroke supported by the textual-based otter TUI.
---

# Otter TUI keyboard reference

This page documents every keystroke the textual-based otter TUI
(`chimera otter --tui`) responds to. Bindings are sourced directly
from `chimera/otter/tui.py`'s `BINDINGS` table and the `Input` widget
defaults — both kept in sync with this doc.

For the broader TUI tour (install, layout, troubleshooting), see
[Otter TUI](./tui.md).

## At a glance

| Keystroke | Action                          | Section                   |
|-----------|---------------------------------|---------------------------|
| `Enter`   | Submit the prompt               | [Submit](#submit)         |
| `Ctrl+C`  | Cancel the in-flight turn       | [Cancel](#cancel)         |
| `Ctrl+D`  | Quit the app                    | [Quit](#quit)             |
| `F1`      | Toggle the help banner          | [Help](#help)             |
| `F2`      | Toggle the right-side panel     | [Side panel](#side-panel) |
| `↑` / `↓` | Move the input caret across lines (single-line input — no-op) | [History](#history) |
| `Home`    | Jump caret to start of input    | [Submit](#submit)         |
| `End`     | Jump caret to end of input      | [Submit](#submit)         |
| `Backspace` | Delete one character left     | [Submit](#submit)         |
| `Delete`  | Delete one character right      | [Submit](#submit)         |

The command bar at the bottom of the screen (textual's `Footer`) shows
the bindings advertised with `show=True` — every entry in the table
above except the input-widget defaults appears there.

## Submit

| Keystroke | Behavior                                                       |
|-----------|----------------------------------------------------------------|
| `Enter`   | Submits the input contents as a new turn via `POST /session/<id>/message`. |
| Any printable key | Inserts that character at the caret. Routed by textual's `Input` widget. |
| `Backspace` / `Delete` / `Home` / `End` | Standard line-edit primitives. |

After submission the input clears and the runtime stores a
`pending_message_id`, which is used by `Ctrl+C` to target the cancel.

## Cancel

| Keystroke | Behavior                                                       |
|-----------|----------------------------------------------------------------|
| `Ctrl+C`  | Calls `OtterServer.cancel_session(session_id)` and surfaces a `(cancel requested)` line in the conversation log. No-op (`(no turn to cancel)`) when nothing is in flight. |

The server's streaming loop checks the cancel flag between every
yielded `LoopEvent`, so cancellation is cooperative — the UI feedback
is immediate; the agent stops at the next yield boundary.

## Quit

| Keystroke | Behavior                                                       |
|-----------|----------------------------------------------------------------|
| `Ctrl+D`  | Fires textual's built-in `quit` action. The app tears down its event-pump thread, unsubscribes from the SSE queue, and exits with return code `0`. |

The terminal returns to your shell with no residual cursor / mode
artefacts; textual restores terminal state on exit.

## Help

| Keystroke | Behavior                                                       |
|-----------|----------------------------------------------------------------|
| `F1`      | Toggles the help banner docked at the top of the screen. Banner content advertises `F1`, `F2`, `Ctrl+C`, `Ctrl+D`. |

Internally the banner has a `hidden` CSS class added/removed by the
`action_toggle_help` method.

## Side panel

| Keystroke | Behavior                                                       |
|-----------|----------------------------------------------------------------|
| `F2`      | Toggles the right-hand side panel that displays per-step tool calls and unrecognised SSE event types. |

The side panel starts visible. Hiding it gives the conversation panel
the full body width, which is handy on narrower terminals.

## History

The current TUI prototype uses textual's single-line `Input` widget,
which does **not** scroll through previously submitted prompts. Up and
down arrows move the caret within a multi-line buffer (no-op here).
Persistent prompt history is tracked on the
[wave-11 backlog](../../research/otter/HANDOFF.md) and not yet wired.

If you need history today, the readline-based REPL (`chimera otter`
without `--tui`) keeps a persistent file at `~/.chimera/otter.history`
and supports `↑` / `↓` natively.

## Source of truth

The bindings above are declared in
`chimera/otter/tui.py`'s `BINDINGS` list:

```python
BINDINGS = [
    Binding("ctrl+c", "cancel_turn", "Cancel turn", show=True),
    Binding("ctrl+d", "quit", "Quit", show=True),
    Binding("f1", "toggle_help", "Help", show=True),
    Binding("f2", "toggle_side_panel", "Side panel", show=True),
]
```

The `Input` widget contributes its own keymap (printable characters,
`Backspace`, `Delete`, `Home`, `End`, `Enter`, caret motion). End-to-end
coverage for the full set lives in `tests/otter/test_tui_smoke.py`
(wave-11 B7).

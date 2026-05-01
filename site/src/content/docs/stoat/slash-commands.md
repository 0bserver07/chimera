---
title: Stoat Slash Commands
description: The stoat REPL slash palette and the headline /shell toggle.
---

# Stoat Slash Commands

The stoat REPL ships seven slash commands. The palette is intentionally
small — the headline ergonomic is the shell-mode toggle, not a thousand
slashes.

| Slash | Effect |
|---|---|
| `/help` | Render this palette inline. |
| `/exit` (alias `/quit`) | Exit the REPL. |
| `/clear` | Drop conversation history (does not affect on-disk eventlog). |
| `/model` (no arg) | Show the active model id. |
| `/model <id>` | Set the active model id for the next agent turn. |
| `/shell` | Toggle between agent mode and shell mode. |
| `/cost` | Show the running cost rolled up from successful agent turns. |
| `/history [N]` | Show the last N submitted lines (default 10), tagged by mode. |

Slash commands are reachable from **both** modes — `/shell` itself has
to be available from inside shell mode to flip you back, and `/help` /
`/exit` work everywhere.

## `/shell` — the headline toggle

```text
stoat> /shell
(shell mode: each input runs as 'bash -c <input>'. Type /shell to return to agent mode.)
stoat$ /shell
(agent mode)
stoat>
```

The slash is functionally equivalent to the `Ctrl-X` keyboard chord
many terminal users expect from upstream shell-mode harnesses, but
unlike a chord it works in every terminal regardless of keybindings.

## `/history` — mode-tagged review

`/history` renders the last few submitted lines with a per-mode marker:

```text
stoat$ /history 4
> list the top-level files
> read the README
$ pwd
$ ls -la
```

`>` for agent-mode lines, `$` for shell-mode lines. The marker matches
the prompt prefix you'd see inline.

## `/model` — mid-session swap

```text
stoat> /model
model: kimi-k2.6
stoat> /model gpt-4o
model set: gpt-4o
stoat>
```

The model swap takes effect on the **next** agent turn — the running
provider isn't recreated until the user actually fires a prompt, so the
slash is cheap and side-effect-free.

## `/cost` — running rollup

```text
stoat> /cost
cost: $0.0421
```

Stoat tracks the running USD cost across successful agent turns in the
current REPL. The rollup is best-effort; turns that complete without a
`cost` field on the agent result aren't counted.

For an authoritative cost rollup across all persisted sessions, use
`chimera stoat sessions cost` (see [`sessions.md`](sessions.md)).

## Embedding

The slash dispatcher lives in `chimera.stoat.slash.SlashPalette`.
Embedders who want to reuse the palette in their own UI can construct
one directly:

```python
from chimera.stoat.shell_mode import ShellModeManager
from chimera.stoat.slash import build_default_palette

palette = build_default_palette(
    shell_mode=ShellModeManager(),
    model="kimi-k2.6",
    on_clear=lambda: print("cleared"),
)
result = palette.dispatch("/shell")
print(result.text)
```

`SlashPalette.dispatch` returns a `SlashResult` with `text` (what to
render), `keep_going` (whether the outer loop should continue), and
`handled` (whether the slash was recognised).

## See also

- [`shell-mode.md`](shell-mode.md) — the toggle in detail.
- [`quickstart.md`](quickstart.md) — install + first run.
- `chimera/stoat/slash.py` — the dispatcher source.

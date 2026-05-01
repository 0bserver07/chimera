---
title: Stoat Shell Mode
description: How the shell-mode toggle works, when to use it, and how to drive it from tests or embedded code.
---

# Stoat Shell Mode

Stoat's headline ergonomic is the **shell-mode toggle**. The same REPL
buffer can either feed the LLM agent or run shell commands directly,
and the user toggles between the two without leaving the prompt.

## The toggle

Three ways to flip:

1. The slash command `/shell` — works in any terminal.
2. The keyboard chord `Ctrl-X` — for terminals that don't intercept it
   for their own bindings (the slash command is always available as a
   fallback).
3. The CLI flag `--shell-mode` — boots stoat directly into shell mode.

When the manager is in **agent mode** (the default) the prompt is
`stoat> ` and each non-slash input is fed to the agent as a turn. When
in **shell mode** the prompt is `stoat$ ` and each non-slash input is
executed as `bash -c <input>` against the current working directory.

```text
stoat> /shell
(shell mode: each input runs as 'bash -c <input>'. Type /shell to return to agent mode.)
stoat$ pwd
/Users/me/proj
stoat$ ls -la
…
stoat$ /shell
(agent mode)
stoat>
```

Slash commands are dispatched regardless of the current mode — `/shell`
itself has to be reachable from inside shell mode to flip you back, and
`/help` / `/exit` etc. work in both.

## Semantics

* **Empty input** in either mode just re-prompts.
* **`/`-prefixed input** runs the slash dispatcher (full palette in
  [`slash-commands.md`](slash-commands.md)).
* **Non-slash input in agent mode** runs `Agent.async_run(input)` against
  the configured provider, with all default tools available.
* **Non-slash input in shell mode** runs `bash -c <input>` (or `$BASH -c`
  if the env var is set) inside the REPL's working directory. stdout
  and stderr are captured and rendered together; the exit code is
  surfaced as a `[exit N]` marker when non-zero.
* **Empty stdout + zero exit** prints `[exit 0]` so the user knows the
  command ran (mirrors what most shells do for "no output, no error").

The two paths share one history buffer, tagged by mode so `/history`
can render them side by side:

```text
stoat$ /history 4
> list the top-level files
> read the README
$ pwd
$ ls -la
```

## Limits

Shell mode is intentionally minimal — it's not a full shell. Specifically:

* **Built-ins like `cd` are not honored.** Each command runs in its own
  `bash -c` subprocess, so `cd ../foo` exits the subprocess immediately
  with no effect on the REPL's working directory. Use `--cwd` (CLI) or
  embed stoat to drive it programmatically if you need long-lived
  directory state.
* **No prompt customisation per directory.** The prompt prefix
  (`stoat$`) is fixed unless you embed `ShellModeManager` in your own
  code and pass a custom `shell_prompt`.
* **No interactive child processes.** `bash -c` captures stdout/stderr
  by default, so `vim`, `top`, `tmux` etc. won't render. Drop to your
  shell for those.

The point isn't to replace your shell — it's to keep tactile shell
moves on hand while you're driving the agent.

## Driving the toggle from code

The state machine lives in `chimera.stoat.shell_mode.ShellModeManager`.
Embedders can use it directly:

```python
from chimera.stoat.shell_mode import ShellModeManager, MODE_SHELL

mgr = ShellModeManager()
mgr.set_mode(MODE_SHELL)
result = mgr.run_shell("ls -la", cwd="/tmp")
print(result.stdout)
print(f"exit: {result.returncode}")
```

The manager is purely state — it doesn't own a REPL loop. The full
loop (prompt → dispatch → render) lives in
`chimera.stoat.repl.StoatRepl`, which is also exposed for embedders
who want the toggle wired into their own UI.

## Driving from tests

`StoatRepl` accepts an `input_fn` parameter so test code can script the
loop without a TTY:

```python
import io
from chimera.stoat.repl import StoatRepl

inputs = iter(["/shell", "echo hi", "/shell", "/exit"])
out = io.StringIO()

repl = StoatRepl(
    model="kimi-k2.6",
    workdir=".",
    out=out,
    input_fn=lambda _prompt: next(inputs),
)
repl.run()
assert "hi" in out.getvalue()
```

The same hook drives the test suite under `tests/stoat/test_repl.py`.

## See also

- [`slash-commands.md`](slash-commands.md) — full slash palette.
- [`quickstart.md`](quickstart.md) — install + first run.
- `chimera/stoat/shell_mode.py` — the state machine source.
- `chimera/stoat/repl.py` — the REPL loop that consumes it.

# `chimera otter` slash commands

The `chimera otter` REPL ships with twenty-five built-in slash
commands, registered in `chimera/otter/slash.py` and merged onto the
shared `chimera.cli.slash_commands` registry. Many delegate to
existing handlers used by `chimera code` and `chimera mink`; the
otter-specific ones (e.g. `/share`) light up otter subsystems via
late-binding so a partial wave-1 build still presents a usable REPL.

## Reference table

| Command         | Args                          | Description                                  | Source             |
|-----------------|-------------------------------|----------------------------------------------|--------------------|
| `/help`         | —                             | Show all registered commands.                | shared             |
| `/exit`         | —                             | Leave the REPL.                              | shared             |
| `/quit`         | —                             | Alias for `/exit`.                           | otter              |
| `/sessions`     | `[list\|<id>]`                | List or switch sessions.                     | otter              |
| `/new`          | —                             | Start a new session (clears context).        | otter              |
| `/clear`        | —                             | Clear the current context.                   | shared             |
| `/share`        | `[--sink file\|http\|stdout]` | Share the current session.                   | otter              |
| `/undo`         | —                             | Undo the last turn (placeholder).            | otter              |
| `/redo`         | —                             | Redo a previously undone turn (placeholder). | otter              |
| `/agent`        | `[<name>]`                    | List or switch the active agent.             | shared             |
| `/agents`       | —                             | List agent presets (alias of `/agent`).      | otter              |
| `/model`        | `[next\|prev\|<name>]`        | Show or cycle the active model.              | shared             |
| `/models`       | `[next\|prev\|<name>]`        | Alias of `/model`.                           | otter              |
| `/tools`        | —                             | List available tools.                        | shared             |
| `/yolo`         | —                             | Toggle bypass-permissions mode.              | shared             |
| `/connect`      | —                             | Connect a provider (placeholder).            | otter              |
| `/mcp`          | —                             | List MCP servers and their tools.            | shared             |
| `/mcps`         | —                             | Alias of `/mcp`.                             | otter              |
| `/status`       | —                             | One-screen summary of the session.           | shared             |
| `/doctor`       | —                             | Environment health checks.                   | shared             |
| `/config`       | —                             | Print the effective merged settings.         | shared             |
| `/cost`         | —                             | Show cumulative cost.                        | shared             |
| `/compact`      | —                             | Compact context (HARD threshold).            | shared             |
| `/init`         | —                             | Summarise the project into `AGENTS.md`.      | shared             |
| `/themes`       | —                             | Switch the REPL theme (placeholder).         | otter              |
| `/edit`         | —                             | Open `$EDITOR` for the next prompt (stub).   | otter              |

The "Source" column distinguishes:

- **shared** — handler defined in `chimera.cli.slash_commands` (or
  `chimera.cli.code`) and reused by both mink and otter.
- **otter** — handler in `chimera/otter/slash.py`, otter-specific.

## Otter-specific commands

### `/sessions`

Lists persisted otter sessions under
`~/.chimera/eventlog/otter-*/`. `/sessions list` prints the table; a
bare `/sessions` defaults to listing. Switching to a previous session
mid-REPL is a wave-2 follow-up; for now use the `chimera otter
sessions show <id>` subcommand to inspect a transcript out of band.

### `/new`

Starts a fresh session. The current context is cleared; agent /
model / cwd are preserved. Equivalent to the upstream `/new`
(aliased to `/clear`).

### `/share`

Packages the current session into a transcript and dispatches it via
`chimera/otter/share_cmd.py`. Three sinks are honored:

- `file` (default) — writes `~/.chimera/shares/otter-<id>.{html,json,md}`.
- `http` — POSTs to `$OTTER_SHARE_URL`.
- `stdout` — prints to the REPL.

The default `$OTTER_SHARE_URL` points at `localhost:5174`; deliberately
not a third-party share endpoint. Operators host their own sink.

### `/undo`, `/redo`, `/edit`, `/themes`

These are placeholder stubs printed with a "not yet wired" notice
pointing at the owning issue. They exist in the registry so that:

1. Tab-completion suggests them, helping users discover what's coming.
2. Documentation stays stable across milestones.
3. The first patch landing the feature only swaps out the handler,
   not the registry shape.

### `/connect`

Opens the provider-connection flow, late-binding to
`chimera/otter/providers.py`. Equivalent to the upstream's
`/connect` slash. When the provider chain is already authenticated
via env vars (`ANTHROPIC_API_KEY`, etc.), `/connect` reports "already
connected via env" rather than re-prompting.

### `/mcps`

Alias of `/mcp`. Both spellings are supported because the upstream
agent exposes `/mcps` (plural) in its TUI command palette while the
shared Chimera registry uses singular `/mcp`. The otter palette wires
both onto the same handler.

## Aliases

The aliases are intentional, mirroring the upstream agent's "common
typo or alternate spelling" tolerance:

| Canonical    | Aliases                          |
|--------------|----------------------------------|
| `/exit`      | `/quit`                          |
| `/clear`     | `/new`                           |
| `/agent`     | `/agents`                        |
| `/model`     | `/models`                        |
| `/mcp`       | `/mcps`                          |
| `/sessions`  | `/resume`, `/continue`           |

## Custom commands

Slash commands defined in `.opencode/command/<name>.md` are loaded by
`chimera/otter/commands.py` and merged onto the same registry (after
the built-ins). They use the `description` frontmatter field as their
help text. See `docs/otter/commands.md` for the file schema.

## Discovery via `/help`

`/help` walks the registry and prints each command name with its help
text. Custom commands are sorted alphabetically after the built-ins.
The output is stable across runs so it's safe to grep for a command
in CI.

## Wiring under the hood

`chimera/otter/slash.py` exports two symbols:

- `OTTER_SLASH_COMMANDS` — `{name: handler}` dict.
- `register_otter_slash(repl_state)` — installer.

The REPL's `_resolve_slash_registry()` helper imports both, calls
`register_otter_slash()` against the shared registry, and falls back
to attribute-style registration for tests that pass a tiny
`SimpleNamespace`-shaped fake.

Each handler follows the standard `(session, env, args, out)`
signature and writes its output through `out` (a `Callable[[str],
None]`). Handlers never raise; they catch unexpected exceptions and
print `failed: <reason>` so the REPL stays usable.

## Test surface

`tests/otter/test_slash.py` covers:

- All twenty-five commands resolvable through
  `OTTER_SLASH_COMMANDS`.
- Each placeholder stub prints its "not yet wired" message and
  doesn't raise.
- Shared-registry passthroughs (`/help`, `/cost`, `/agent`, ...)
  delegate to the canonical handlers.
- `register_otter_slash()` against three REPL state shapes:
  `register(...)` method, `commands` mapping, plain object with
  `setattr` fallback.

## See also

- `chimera/otter/slash.py` — implementation.
- `docs/otter/commands.md` — custom commands ingest.
- `docs/otter/agents.md` — agent presets (`/agent`).
- `docs/mink/slash-commands.md` — the mink twin (most handlers shared).

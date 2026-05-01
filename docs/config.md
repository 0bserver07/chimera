# `chimera config` — persistent CLI defaults

`chimera config` stores user defaults for any of the seven coding-agent CLIs
(mink, otter, ferret, weasel, shrew, stoat, badger) plus a cross-cutting
`global` namespace. The on-disk format is a small TOML file at
`~/.chimera/config.toml` (override with `$CHIMERA_CONFIG_HOME`).

Stdlib only — reads via `tomllib`, writes via a hand-rolled emitter. No
runtime dependencies.

## Quick reference

```sh
chimera config set otter.model glm-5
chimera config set mink.permission-mode auto
chimera config set shrew.vram-gb 16
chimera config set global.no-color true

chimera config get otter.model            # -> glm-5
chimera config list                       # all keys
chimera config list --cli otter           # only the [otter] table
chimera config unset otter.model
chimera config edit                       # opens $EDITOR on the toml
```

## Key shape

Keys are dot-namespaced. The prefix before the first `.` selects a TOML
table; everything after becomes the leaf key inside that table:

| Key                       | Lands in TOML as                          |
| ------------------------- | ----------------------------------------- |
| `otter.model`             | `[otter] model = "..."`                   |
| `mink.permission-mode`    | `[mink] permission-mode = "..."`          |
| `shrew.vram-gb`           | `[shrew] vram-gb = 16`                    |
| `global.no-color`         | `[global] no-color = true`                |
| `no-color` (no dot)       | `[global] no-color = true` (auto-bucketed) |

Recognised tables for `--cli` filtering: `global`, `mink`, `otter`,
`ferret`, `weasel`, `shrew`, `stoat`, `badger`. Unknown tables are
permitted in the file (forward-compat) but won't be offered as completions.

## Value coercion

`config set <key> <value>` parses the right-hand side heuristically:

1. `true` / `false` (case-insensitive) → boolean.
2. Anything that parses as a Python `int` → integer.
3. Otherwise → string (stored verbatim, no quoting required at the shell).

Floats are intentionally *not* auto-coerced — most config values are
discrete (model names, permission modes, integer budgets), and silently
turning `"1.0"` into a float would surprise users who expect to round-trip
the literal string.

## Reading defaults from your CLI code

The companion helper `chimera/cli/config_loader.py` exposes a single
function:

```python
from chimera.cli.config_loader import resolve_default

model = resolve_default("otter", "model", fallback="claude-sonnet-4")
no_color = resolve_default("global", "no-color", fallback=False)
```

`resolve_default(cli, key, fallback)` looks up `[<cli>] <key>` first, then
falls through to `[global] <key>`, then returns `fallback`. The helper is
deliberately not yet wired into every CLI's argument parser — wave 11
ships only the storage layer; per-CLI adoption is a follow-up.

## File layout

```toml
[global]
no-color = true

[mink]
permission-mode = "auto"

[otter]
model = "glm-5"

[shrew]
vram-gb = 16
```

Tables and keys are emitted in sorted order so `git diff` stays readable.
Empty tables are dropped on save.

## Editing

`chimera config edit` opens the file in `$EDITOR`. If `$EDITOR` is unset,
the command exits with code 2 rather than guessing — explicit configuration
beats an editor surprise on a remote box. The file is created on first run
if it doesn't already exist.

## Testing locally

The test suite redirects `$HOME` to a `tmp_path` fixture, so running
`uv run pytest tests/cli/test_config_cmd.py` never touches your real
`~/.chimera/config.toml`. To sandbox manually, set
`CHIMERA_CONFIG_HOME=/tmp/chi-test` before invoking the CLI.

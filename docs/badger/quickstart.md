# Badger Quickstart

Badger is the seventh Chimera coding-agent CLI. It mirrors a
**harness-rewrite** posture: tighter step budget, focused tool surface,
and rerun-on-failure discipline as a first-class concern. Where mink
mirrors a TUI-first ergonomic and ferret mirrors a sandbox-first /
IDE-first posture, badger is what you reach for when "better harness
tools" is the load-bearing requirement. Run `chimera badger --help-long`
to see verbose per-flag descriptions; `--help` itself stays under 50
lines for scannability.

## Install

```bash
uv sync --extra dev --extra anthropic
```

## One-shot prompt

```bash
chimera badger -p "Refactor src/util.py to remove duplicated string formatting"
```

The default model is `claude-sonnet-4-6`. Override with `--model` or
`$BADGER_MODEL`.

## Interactive REPL

```bash
chimera badger
```

You drop into the shared Chimera REPL with the badger slash palette
(see [parity-matrix.md](./parity-matrix.md) for the full list).

## Harness-first defaults

Badger ships with deliberately tight defaults:

| Knob              | Badger | Other CLIs       |
| ----------------- | ------ | ---------------- |
| `--max-steps`     | 25     | 50               |
| Tool surface      | full set, easily restricted via `--allowed-tools` | full set |
| Rerun-on-failure  | opt-in (`--rerun-on-failure`) with up to 2 reruns | not exposed |

The intent: prefer rerun discipline over runaway loops. See
[harness-discipline.md](./harness-discipline.md) for the rationale.

## Permission mode (5-mode standard)

Badger exposes the cross-CLI `--permission-mode` flag (shared with
`chimera ferret` and `chimera mink`). Pick a stance that matches the
risk envelope of the run; the same five values work identically across
all three CLIs.

| Mode         | Reads | Edits | Bash / Git | Notes                                |
| ------------ | ----- | ----- | ---------- | ------------------------------------ |
| `read-only`  | allow | deny  | deny       | Pure planning / review.              |
| `suggest`    | allow | ask   | ask        | "Show your work" — the default.      |
| `auto`       | allow | allow | ask        | Fast iteration; pair with a sandbox. |
| `yolo`       | allow | allow | allow      | Use only inside a sandbox.           |
| `strict`     | ask   | ask   | ask        | Confirm every single tool call.      |

```bash
chimera badger -p "Touch nothing, just summarise" --permission-mode read-only
chimera badger -p "Refactor util.py" --permission-mode auto
```

The default is `suggest`. Mappings to the underlying
`PermissionPolicy` presets live in
`chimera.permissions.modes.policy_for_mode`.

## Subcommands

```bash
chimera badger sessions list           # show persisted runs
chimera badger sessions show <id>      # transcript
chimera badger parity --against PARITY.md   # parity check
chimera badger share <session>         # export tarball
```

## What's next

- [Harness Discipline](./harness-discipline.md) — why max-steps=25 and
  why rerun-on-failure is opt-in.
- [Parity](./parity.md) — declare a target schema and diff live.
- [Rerun on Failure](./rerun-on-failure.md) — markers, refinements,
  budgets.
- [Providers](./providers.md) — Anthropic-first chain with sane
  fallbacks.
- [Parity Matrix](./parity-matrix.md) — flag/command surface vs sibling
  CLIs.
- [Security and Trademarks](./security-and-trademarks.md) — naming
  hygiene, allowed paths.

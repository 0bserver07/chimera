---
title: Badger Quickstart
description: Install badger, run a harness-rewrite one-shot turn, drive the rerun budget, the parity tracker, and the full /git + /memory + /bughunter + /ultraplan + /teleport slash family.
---

# `chimera badger` Quickstart

`chimera badger` is the seventh Chimera coding-agent CLI. Where
[`chimera mink`](../mink/quickstart.md) ships a TUI-first ergonomic and
[`chimera ferret`](../ferret/quickstart.md) ships an IDE-first sandboxed
posture, **badger ships a harness-rewrite posture**: tighter step budget
(`--max-steps 25`), rerun-on-failure discipline as a first-class concern,
a parity-tracker subcommand that diffs the live surface against a schema
file, and the largest slash palette of any Chimera coding CLI (28
commands including the full `/git` family, `/memory`, `/bughunter`,
`/ultraplan`, and `/teleport`).

Deeper dives:

- [`harness-discipline.md`](harness-discipline.md) — why max-steps=25.
- [`rerun-on-failure.md`](rerun-on-failure.md) — failure markers, budgets.
- [`parity.md`](parity.md) — declare a schema and diff live.
- [`providers.md`](providers.md) — Anthropic-first chain.
- [`parity-matrix.md`](parity-matrix.md) — surface vs sibling CLIs.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- One of: an Anthropic key, an OpenAI key, an OpenRouter key, or an
  Ollama account (`OLLAMA_API_KEY` for `:cloud` tags)

```bash
uv --version                          # >= 0.4
uv sync --extra dev --extra anthropic # core + Anthropic SDK
```

## Provider configuration

Badger resolves the provider in this order (first match wins):

1. `--model <id>` on the CLI.
2. `$BADGER_MODEL` environment variable.
3. `$ANTHROPIC_API_KEY` → defaults to `claude-sonnet-4-6`.
4. `$OPENAI_API_KEY` → defaults to `gpt-4o`.
5. `$OPENROUTER_API_KEY` → defaults to `anthropic/claude-sonnet-4`.
6. `$OLLAMA_API_KEY` → defaults to `gpt-oss:120b-cloud`.
7. A reachable Ollama daemon on `:11434` → first installed tag.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# OR
export OLLAMA_API_KEY=<your-key>
export OLLAMA_HOST=https://ollama.com
```

## First one-shot turn

```bash
chimera badger -p "list the top-level files and read the README"
```

Expected output shape (truncated):

```text
I'll list the repo first, then read the README.

▶ list_files(path=".")
CHANGELOG.md  CLAUDE.md  README.md  chimera/  docs/  examples/  tests/

▶ Read(path="README.md")
# Chimera
A composable coding agent framework
...

The repo root has a README pitching Chimera as a composable coding agent framework.
```

Useful one-shot flags:

```bash
chimera badger --model gpt-oss:120b-cloud -p "draft a release note"
chimera badger --output-format json -p "ship it"
chimera badger --max-steps 10 -p "summarize"
chimera badger --allowed-tools Read,Bash -p "audit"
chimera badger --no-save -p "ad-hoc, don't journal"
chimera badger --no-color -p "..." | tee badger.log
```

## Drop into the REPL

Run `chimera badger` with no `-p` flag for the interactive REPL:

```text
badger — Chimera coding agent (harness-rewrite posture)
model: claude-sonnet-4-6  ·  max-steps: 25  ·  cwd: /Users/me/proj
Type /help for commands, /exit to quit.
badger> /tools
Read, Write, Edit, Bash, Grep, Glob, TodoWrite, list_files, ...
badger> /git status
## master...origin/master
 M chimera/badger/repl.py
badger> /teleport build_provider
/teleport: 1 result(s) for 'build_provider'
  chimera/badger/providers.py:18    def build_provider(...) -> Provider:
badger> refactor providers.py to extract the env-resolution helper
…
```

## Harness-first defaults

| Knob              | Badger | Other CLIs       |
| ----------------- | ------ | ---------------- |
| `--max-steps`     | 25     | 50 (mink/otter)  |
| Rerun-on-failure  | opt-in (`--rerun-on-failure`, ≤ 2 reruns) | not exposed |
| Permission mode   | `suggest` (5-mode standard) | varies |

## `--rerun-on-failure` walkthrough

When the agent's first attempt shows a failure marker (`pytest` failed,
`mypy` errored, a syntax error in a written file), badger resets the
conversation, refines the prompt with the captured failure, and runs
again — up to `--max-reruns` extra attempts. Total ≤ 1 + `--max-reruns`.

```bash
# Default budget — one rerun on a failure marker.
chimera badger --rerun-on-failure \
               -p "fix the failing test in tests/test_util.py"

# Wider budget — three reruns.
chimera badger --rerun-on-failure --max-reruns 3 \
               -p "regenerate broken type hints across chimera/core/"
```

Inside the REPL the same knob is a slash command:

```text
badger> /rerun
/rerun: rerun_on_failure=False max_reruns=2
badger> /rerun 3
/rerun: enabled, max_reruns=3
badger> /rerun off
/rerun: disabled
```

## `--profile` usage

Discipline presets bundle the common knob combinations:

| Profile | `--max-steps` | Permissions | Rerun |
| ------- | ------------- | ----------- | ----- |
| `strict`   | 15 | strict (ask every tool call) | off |
| `balanced` | 25 | suggest (ask for writes + shell) | on (1) |
| `yolo`     | 50 | yolo (no asks) | on (3) |

```bash
chimera badger --profile strict   -p "explain the OAuth flow"
chimera badger --profile balanced -p "fix this failing test"
chimera badger --profile yolo     -p "ship it"
```

Profiles always lose to explicit flags.

## Parity tracking

`chimera badger parity` diffs the live CLI surface against a schema
file. Catches drift between an intended surface (declared in
`PARITY.md` or `PARITY.json`) and the shipped one.

```bash
chimera badger parity                                    # auto-resolve
chimera badger parity --against docs/badger/PARITY.md    # explicit path
```

Sample output:

```text
PARITY REPORT  (schema: PARITY.md)
────────────────────────────────────
flags        ✓  21 / 21 present
slashes      ✓  28 / 28 present
subcommands  ⚠  3 / 4 present  (missing: bench)
exit codes   ✓  4 / 4 documented
────────────────────────────────────
verdict: 1 drift, see lines above
```

Inside the REPL the same check is wired as `/parity`.

## Slash commands

The non-trivial palette entries, grouped by purpose:

**`/git` family** — six native wrappers (added W14-4)

```text
badger> /git status
## master...origin/master
 M chimera/badger/repl.py

badger> /git log 3
8df78c3 docs(teams): refresh agent-teams.md
8b65604 docs(ollama,inspirations): document Path A
2234c91 fix(shrew,stoat): respect OLLAMA_HOST

badger> /git commit -m "wire BADGER_MODEL fallback"
[master 4a3b1d2] wire BADGER_MODEL fallback
```

**`/memory`** — per-CLI scratch notes at `~/.chimera/badger/memory.md`

```text
badger> /memory edit
/memory: edited /Users/me/.chimera/badger/memory.md
badger> /memory append "Investigate rerun budget overflow #214"
/memory: appended to /Users/me/.chimera/badger/memory.md
```

**`/bughunter`** — queue a multi-perspective bug-hunt prompt

```text
badger> /bughunter chimera/core/
/bughunter: queued bug-hunt workflow (487 chars)
```

**`/ultraplan`** — five-phase planning prompt (no edits this turn)

```text
badger> /ultraplan migrate auth from session-cookies to JWT
/ultraplan: queued five-phase plan for 'migrate auth from session-cookies to JWT'
```

**`/teleport`** — symbol/path resolver (Python, JS/TS, Rust)

```text
badger> /teleport build_provider
/teleport: 1 result(s) for 'build_provider'
  chimera/badger/providers.py:18    def build_provider(... ) -> Provider:
```

Type `/help` at any prompt for the full 28-entry list.

## Sessions / persistence

```bash
chimera badger sessions list
chimera badger sessions list --all-clis    # include otter/ferret/weasel/...
chimera badger sessions show badger-20260514T091201-71032a5e
chimera badger share badger-20260514T091201-71032a5e
```

## Choose your model

Recommended models for the harness-rewrite posture (frequent tool
calls, rerun-loop stability):

| Backend | Tag | Why for badger |
|---|---|---|
| Anthropic | `claude-sonnet-4-6` | Strongest tool calling; default. |
| Ollama Cloud | `gpt-oss:120b-cloud` | Free with Ollama account; native tools. |
| Ollama Cloud | `kimi-k2.6:cloud` | 256k context; preserves reasoning between reruns. |
| OpenAI | `gpt-4o` | Strong baseline; `$OPENAI_API_KEY`. |

See [the Ollama Cloud recipe](../use-with-ollama.md) for the auth
handshake.

```bash
OLLAMA_HOST=https://ollama.com OLLAMA_API_KEY=$KEY \
chimera badger --model gpt-oss:120b-cloud -p "audit list of files"
```

## Real task: rerun a failing test until it passes

```bash
chimera badger --profile balanced \
               --rerun-on-failure --max-reruns 2 \
               -p "fix tests/test_util.py::test_format_currency"
```

Expected shape:

```text
[badger] attempt 1/3
▶ Bash$ uv run pytest tests/test_util.py::test_format_currency -x
FAILED  AssertionError: expected '$1,234.56', got '$1234.56'

I see — the formatter is missing the comma. Patching util.py…
▶ Edit(file=src/util.py, …)
▶ Bash$ uv run pytest tests/test_util.py::test_format_currency -x
PASSED  1 passed in 0.04s

[badger] attempt 1 succeeded. Stop.
```

If the first attempt fails, badger fires a fresh conversation with the
captured failure as context. Compare with mink, where you'd drive the
recovery manually.

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `BADGER_MODEL` | (unset) | Default model id. |
| `ANTHROPIC_API_KEY` | (unset) | Anthropic provider chain. |
| `OPENAI_API_KEY` | (unset) | OpenAI provider chain. |
| `OLLAMA_API_KEY` | (unset) | Ollama Cloud `:cloud` tags. |
| `OLLAMA_HOST` | `http://localhost:11434` | Daemon URL. |
| `CHIMERA_HOME` | `~/.chimera` | Root for eventlog, exports, memory. |
| `NO_COLOR` | (unset) | Plain output handler. |

## Where to go next

- [Harness Discipline](./harness-discipline.md)
- [Rerun on Failure](./rerun-on-failure.md)
- [Parity](./parity.md) and [Parity Matrix](./parity-matrix.md)
- [Providers](./providers.md)
- [Security and Trademarks](./security-and-trademarks.md)

---

### Verified (2026-05-14)

Two commands from this quickstart, against Ollama Cloud
(`gpt-oss:120b-cloud`):

```text
$ OLLAMA_HOST=https://ollama.com OLLAMA_API_KEY=*** \
    chimera badger --model gpt-oss:120b-cloud \
                   -p "what is 2 plus 2" --max-steps 3 --no-color --no-save
2 + 2 = 4.

$ chimera badger sessions list
SESSION_ID                              DATE              MODEL               STEPS  COST      OK   PROMPT
(no persisted badger sessions found)

$ chimera badger --version
chimera badger 0.7.0
```

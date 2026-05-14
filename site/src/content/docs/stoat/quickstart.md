---
title: Stoat Quickstart
description: Install stoat, drive the agent/shell toggle (slash and Ctrl-X chord), walk the plan-mode posture, register subagents, and learn --continue/--session.
---

# `chimera stoat` Quickstart

`chimera stoat` is the sixth Chimera coding-agent CLI. Where
[`chimera weasel`](../weasel/quickstart.md) ships a four-mode minimal
harness and [`chimera shrew`](../shrew/quickstart.md) tunes for small
local models, **stoat ships a shell-mode toggle**: in the same REPL,
each line either feeds the LLM agent or runs as a direct shell command,
and you flip between the two without leaving the prompt.

A single slash (`/shell`) — or the `Ctrl-X s` chord when
`prompt_toolkit` is installed — toggles between agent mode (`stoat>`)
and shell mode (`stoat$`). A second posture, `plan` mode, freezes the
agent into a planner that produces a plan and asks for confirmation
rather than acting. Plans persist to `~/.chimera/plans/` and can be
resumed.

Deeper dives:

- [`shell-mode.md`](shell-mode.md) — the toggle in detail.
- [`slash-commands.md`](slash-commands.md) — the slash palette.
- [`providers.md`](providers.md) — Kimi-first provider chain.
- [`sessions.md`](sessions.md) — list / show / cost / share.
- [`parity-matrix.md`](parity-matrix.md) — surface mapping.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- One of: a Moonshot key, an Anthropic key, an OpenAI key, an
  OpenRouter key, or a reachable Ollama daemon / cloud account

```bash
uv --version                          # >= 0.4
uv sync --extra dev --extra anthropic # core + Anthropic SDK
```

For the keyboard chords (`Ctrl-X s` / `Ctrl-X p` / `Ctrl-X h`), install
the optional readline replacement:

```bash
uv pip install prompt_toolkit
```

Without it, stoat falls back to plain `input()` and only the slash
forms work.

## Provider configuration

Stoat's chain is **Kimi-first** because the shell-toggle workflow is
tuned for Kimi K2.6 chat models. Resolution order (first match wins):

1. `--model <id>` on the CLI.
2. `$STOAT_MODEL` environment variable.
3. `$MOONSHOT_API_KEY` set → defaults to `kimi-k2.6`.
4. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
5. `$OPENAI_API_KEY` set → defaults to `gpt-4o`.
6. `$OPENROUTER_API_KEY` set → defaults to `moonshot/kimi-k2.6`.
7. `$OLLAMA_API_KEY` set → defaults to `kimi-k2.6:cloud` (Ollama Cloud).
8. Friendly error pointing at the env vars above.

```bash
export MOONSHOT_API_KEY=...
# OR — recommended free path
export OLLAMA_HOST=https://ollama.com
export OLLAMA_API_KEY=<your-key>
```

## First run — the toggle

```bash
chimera stoat
```

```text
stoat — Chimera coding agent (shell-mode toggle: /shell)
model: kimi-k2.6  ·  mode: agent  ·  cwd: /Users/me/proj
Type /help for commands, /exit to quit.
stoat> list the top-level files and read the README
I'll list the repo first, then read the README.
…
stoat> /shell
(shell mode: each input runs as 'bash -c <input>'. Type /shell to return to agent mode.)
stoat$ ls
CHANGELOG.md  CLAUDE.md  README.md  chimera/  docs/  examples/  tests/
stoat$ git status
On branch master
…
stoat$ /shell
(agent mode)
stoat> commit the staged changes with a one-line message
…
```

Two lines, two modes. The same buffer, no extra terminal.

Boot straight into shell mode:

```bash
chimera stoat --shell-mode
```

## The Ctrl-X chord

When `prompt_toolkit` is installed, three `Ctrl-X <key>` chords are
wired:

| Chord     | Effect                                 |
| --------- | -------------------------------------- |
| `Ctrl-X s` | Toggle shell ⇔ agent mode (same as `/shell`). |
| `Ctrl-X p` | Toggle plan mode (same as `/plan`).    |
| `Ctrl-X h` | Show inline help (same as `/help`).    |

The chord is consumed inline — the buffer continues for the next
character, so you can type a chord mid-prompt and the partial line is
preserved. Without `prompt_toolkit`, the chord is a no-op and the
slash forms remain the only path.

Tip: stoat supports **bracketed paste** via the same `prompt_toolkit`
input adapter, so pasting a multi-line snippet doesn't trigger the
mode toggle even if it happens to contain `\x18` (Ctrl-X) bytes.

## Plan mode walkthrough

Plan mode swaps the system prompt so the model emits a structured plan
and waits for confirmation. No edit / write / bash tools fire on plan
turns — only read-only inspection.

```text
stoat> /plan
(plan mode: agent will plan, not act. Toggle with /plan or Ctrl-X p.)
stoat> refactor auth from session cookies to JWT
PLAN  (id: plan-20260514T120505-a1b2)
  Phase 1 — survey
    [r] read chimera/auth/*.py
    [r] grep 'session_id' tests/
  Phase 2 — shape
    new JWTAuth class with refresh-rotation support
    migrate AuthMiddleware to call JWTAuth.validate()
  Phase 3 — migrate
    backfill existing sessions with a one-time JWT exchange endpoint
    24-hour grandfather window
  Phase 4 — verify
    add tests/test_jwt_auth.py
    run uv run pytest -k auth
Confirm with /plan accept or /plan abort.
stoat> /plan accept
(agent mode — plan persisted to ~/.chimera/plans/plan-20260514T120505-a1b2.json)
stoat> 
```

Saved plans:

```bash
chimera stoat sessions list                 # includes plan IDs
ls ~/.chimera/plans/                        # raw plan files
chimera stoat --session plan-20260514T120505-a1b2  # resume
```

## Slash commands

Stoat keeps the slash palette deliberately small. The nine commands:

```text
/help                  Show this help message.
/exit                  Exit the REPL.
/clear                 Reset conversation history.
/model [<id>]          Show or set the active model id.
/shell                 Toggle agent ⇔ shell mode.  ← headline
/plan                  Toggle plan mode (planner posture, no actions).
/cost                  Show running cost for the current session.
/history [<n>]         Show the last n submitted lines (default 10).
/sessions [list|show]  List recent stoat sessions or show one by id.
```

Worked examples:

```text
stoat> /model
model: kimi-k2.6
stoat> /model gpt-oss:120b-cloud
model: gpt-oss:120b-cloud
stoat> /cost
session cost: $0.0072  (4 turns, 2 tool calls)
stoat> /history 3
1: list the top-level files
2: /shell
3: ls
stoat> /sessions list
SESSION_ID                              DATE              MODEL              PROMPT
stoat-20260514T120505-a1b2              2026-05-14 12:05  kimi-k2.6          refactor auth from …
```

## Subagent registry

Stoat exposes a `chimera stoat agents` subcommand that walks the same
project > user > built-in chain as the other CLIs, plus four
stoat-specific defaults that pre-tune the system prompt for the
shell-toggle ergonomic:

```bash
chimera stoat agents list
```

```text
NAME       SOURCE   MODEL              DESCRIPTION
shell      builtin  -                  Lives in shell mode, escalates to agent only on demand.
planner    builtin  -                  Plan-only profile (forces /plan on every turn).
review     builtin  -                  Read-only code review; no edits.
build      builtin  -                  Full build profile (read/write/edit/bash + tests).
```

Inspect one:

```bash
chimera stoat agents show planner
```

Use one for a single run:

```bash
chimera stoat -p "audit auth module" --agent review
chimera stoat --agent planner       # boots in plan mode automatically
```

## --continue and --session

Two persistence flags for resuming work:

```bash
# Resume the newest stoat session for this cwd.
chimera stoat -c

# Resume an explicit session id (wins over --continue).
chimera stoat --session stoat-20260514T120505-a1b2

# Resume in plan-mode posture from a saved plan id.
chimera stoat --session plan-20260514T120505-a1b2 --plan-mode
```

Either flag drops you back at the prompt with the full conversation
history rehydrated; tools, model, and mode are restored exactly.

## One-shot print mode

`-p` runs a single agent turn and exits. Plain text on stdout by
default, JSON with `--json`:

```bash
chimera stoat -p "list the top-level files and read the README"
chimera stoat -p "summarize TODO comments in src/" --json
chimera stoat -p "ship it" --max-steps 5
chimera stoat --model gpt-oss:120b-cloud -p "draft a release note"
chimera stoat -p "audit" --allowed-tools Read,Bash
```

Print mode does not engage shell mode — it always runs the agent loop.
For one-off shell commands, use your shell directly; that's the whole
point of stoat's toggle.

## Sessions and shares

```bash
chimera stoat sessions list
chimera stoat sessions list --all-clis              # include every Chimera CLI
chimera stoat sessions show stoat-20260514T120505-a1b2
chimera stoat sessions cost --since 7d
chimera stoat share stoat-20260514T120505-a1b2 --share-format md
```

The `cost` rollup re-uses the canonical schema from
[`chimera mink runs cost`](../mink/runs.md) so JSON / CSV / text is
byte-identical across all CLIs.

## Choose your model

Recommended models for the shell-toggle ergonomic (low-latency,
short-turn-friendly):

| Backend | Tag | Why for stoat |
|---|---|---|
| Moonshot | `kimi-k2.6` | Default; tuned for the shell-toggle ergonomic. |
| Ollama Cloud | `kimi-k2.6:cloud` | Same model, free with an Ollama account. |
| Ollama Cloud | `gpt-oss:120b-cloud` | Native tools; fast first token. |
| Anthropic | `claude-sonnet-4-6` | Strongest tool calling fallback. |

See [the Ollama Cloud recipe](../use-with-ollama.md) for the auth
handshake.

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `STOAT_MODEL` | (unset) | Default model id. |
| `MOONSHOT_API_KEY` | (unset) | Activates the Kimi-first chain. |
| `MOONSHOT_BASE_URL` | `https://api.moonshot.ai/v1` | Override for self-hosted gateways. |
| `ANTHROPIC_API_KEY` | (unset) | Anthropic chain. |
| `OPENAI_API_KEY` | (unset) | OpenAI chain. |
| `OPENROUTER_API_KEY` | (unset) | OpenRouter chain. |
| `OLLAMA_API_KEY` | (unset) | Ollama Cloud (`:cloud` tags). |
| `OLLAMA_HOST` | `http://localhost:11434` | Daemon URL. |
| `BASH` | `bash` | Override bash binary for shell mode. |
| `NO_COLOR` | (unset) | Plain output handler. |

## What gets written to disk

| Path | What |
|---|---|
| `~/.chimera/eventlog/stoat-<id>/` | Per-run event stream + summary. |
| `~/.chimera/plans/plan-<id>.json` | Plan-mode artifacts (resumable). |
| `~/.chimera/shares/stoat-<id>.<ext>` | Rendered share files. |
| `~/.chimera/credentials.json` | OAuth tokens (mode `0o600`). |

Everything is local-only. Purge with
`rm -rf ~/.chimera/eventlog/stoat-* ~/.chimera/plans/plan-*`.

## Where to go next

- [`shell-mode.md`](shell-mode.md) — master the toggle.
- [`slash-commands.md`](slash-commands.md) — full palette.
- [`providers.md`](providers.md) — pick a provider.
- [`parity-matrix.md`](parity-matrix.md) — surface mapping.
- [`security-and-trademarks.md`](security-and-trademarks.md) — policy.

---

### Verified (2026-05-14)

Two commands from this quickstart, against Ollama Cloud:

```text
$ OLLAMA_HOST=https://ollama.com OLLAMA_API_KEY=*** \
    chimera stoat -p "Hello, please reply with one word: hello" \
                  --model gpt-oss:120b-cloud --max-steps 2 --no-color
hello

$ chimera stoat --version
chimera stoat 0.7.0
```

---
title: Stoat Quickstart
description: Install stoat, learn the shell-mode toggle, and run your first agent + shell turns side by side.
---

# `chimera stoat` Quickstart

`chimera stoat` is the sixth Chimera coding-agent CLI. Where
[`chimera weasel`](../weasel/quickstart.md) ships a four-mode minimal
harness and [`chimera shrew`](../shrew/quickstart.md) tunes for small
local models, **stoat ships a shell-mode toggle**: in the same REPL,
each line either feeds the LLM agent or runs as a direct shell command,
and the user flips between the two without leaving the prompt.

The headline trade is ergonomic. Stoat is for users who live in their
terminal and want one buffer for both `ls -la` and "explain this repo".
A single slash (`/shell`) toggles between agent mode (`stoat>`) and
shell mode (`stoat$`); each input runs as `bash -c <input>` until the
user toggles back.

This page walks through installation, the toggle, and the headline
subcommands. Deeper dives:

- [`shell-mode.md`](shell-mode.md) — the toggle in detail.
- [`slash-commands.md`](slash-commands.md) — the slash palette.
- [`providers.md`](providers.md) — Kimi-first provider chain.
- [`sessions.md`](sessions.md) — list / show / cost / share.
- [`parity-matrix.md`](parity-matrix.md) — surface mapping vs upstream.
- [`security-and-trademarks.md`](security-and-trademarks.md) — policy.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- One of: a Moonshot API key (recommended for the upstream-tuned
  defaults), an Anthropic key, an OpenAI key, an OpenRouter key, or a
  reachable Ollama daemon

```bash
uv --version                          # >= 0.4
uv sync --extra dev --extra anthropic # core + Anthropic SDK
```

## Provider configuration

Stoat's provider chain is **Kimi-first** because the upstream
shell-mode-toggle harness is tuned for Kimi K2.6 chat models. Resolution
order (first match wins):

1. `--model <id>` on the CLI.
2. `$STOAT_MODEL` environment variable.
3. `$MOONSHOT_API_KEY` set → defaults to `kimi-k2.6` (routed through the
   OpenAI-compatible adapter against `api.moonshot.ai`).
4. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
5. `$OPENAI_API_KEY` set → defaults to `gpt-4o`.
6. `$OPENROUTER_API_KEY` set → defaults to `moonshot/kimi-k2.6`.
7. `$OLLAMA_API_KEY` set → defaults to `qwen3.5:cloud`.
8. Friendly error pointing at the env vars above.

```bash
export MOONSHOT_API_KEY=...
# OR
export ANTHROPIC_API_KEY=sk-ant-...
# OR
export OPENAI_API_KEY=sk-...
```

The Moonshot path is OpenAI-compatible; you can override
`$MOONSHOT_BASE_URL` for self-hosted gateways. Full chain detail in
[`providers.md`](providers.md).

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

Two lines, two modes. The same buffer; no extra terminal, no `subshell`,
no escape characters. `/shell` is the only ceremony.

You can also boot stoat directly into shell mode:

```bash
chimera stoat --shell-mode
```

…then `/shell` flips you back into agent mode whenever you want the LLM.

## One-shot print mode

`-p` runs a single agent turn and exits. Plain text on stdout by default,
JSON with `--json`:

```bash
chimera stoat -p "list the top-level files and read the README"
chimera stoat -p "summarize TODO comments in src/" --json
chimera stoat -p "ship it" --max-steps 5
chimera stoat --model gpt-4o -p "draft a release note"
chimera stoat -p "audit" --allowed-tools Read,Bash
```

Print mode does not engage shell mode — it always runs the agent loop.
For one-off shell commands, use your shell directly; that's the whole
point of stoat's toggle.

## Sessions and shares

Every interactive run journals to
`~/.chimera/eventlog/stoat-<utc>-<uuid>/`. List, inspect, cost, and
share with the same UX as the other coding-agent CLIs:

```bash
chimera stoat sessions list
chimera stoat sessions show stoat-20260430T101455-1f3c2a8b
chimera stoat sessions cost --since 7d
chimera stoat share stoat-20260430T101455-1f3c2a8b --share-format md
```

The `cost` rollup re-uses [`chimera mink runs cost`](../mink/runs.md)
under the hood so the JSON / CSV / text schema is byte-identical
across all six CLIs.

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `STOAT_MODEL` | (unset) | Default model id when `--model` is not passed. |
| `MOONSHOT_API_KEY` | (unset) | Activates the Kimi-first provider path. |
| `MOONSHOT_BASE_URL` | `https://api.moonshot.ai/v1` | Override for self-hosted gateways. |
| `ANTHROPIC_API_KEY` | (unset) | Activates the Anthropic chain. |
| `OPENAI_API_KEY` | (unset) | Activates the OpenAI chain. |
| `OPENROUTER_API_KEY` | (unset) | Activates the OpenRouter (compatible) chain. |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama daemon root for remote daemons. |
| `BASH` | `bash` | Override the bash binary used in shell mode. |
| `NO_COLOR` | (unset) | Force the plain output handler. |

## What gets written to disk

| Path | What |
|---|---|
| `~/.chimera/eventlog/stoat-<id>/` | Per-run event stream + summary. |
| `~/.chimera/shares/stoat-<id>.<ext>` | Rendered share files. |
| `~/.chimera/credentials.json` | OAuth-issued tokens (mode `0o600`). |

Everything is local and plaintext. No remote telemetry. Purge old runs
with `rm -rf ~/.chimera/eventlog/stoat-*`.

## Where to go next

- Master the toggle: [`shell-mode.md`](shell-mode.md).
- Inventory the slash palette: [`slash-commands.md`](slash-commands.md).
- Pick a provider: [`providers.md`](providers.md).
- Walk the surface-by-surface upstream parity: [`parity-matrix.md`](parity-matrix.md).
- Trademark + security policy: [`security-and-trademarks.md`](security-and-trademarks.md).

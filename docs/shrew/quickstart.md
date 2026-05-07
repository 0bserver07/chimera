---
title: Shrew Quickstart
description: Install shrew, run a small-model coding session against a local llama.cpp server, and learn the three flags that matter most — model, vram-gb, no-skills.
---

# `chimera shrew` Quickstart

`chimera shrew` is the fifth Chimera coding-agent CLI and the first
one tuned **explicitly for small local models**. Where
[`chimera mink`](../mink/quickstart.md) mirrors a TUI-first agent,
[`chimera otter`](../otter/quickstart.md) mirrors a server-first
multi-client agent, and [`chimera ferret`](../ferret/quickstart.md)
mirrors an IDE-first sandboxed agent, shrew mirrors a small-model
coding agent — a thin layer on top of
[`chimera weasel`](../weasel/quickstart.md) that pins three
small-model defaults, ships a curated skill set, and adds a
benchmark harness for Aider Polyglot and GAIA. Run `chimera shrew
--help-long` to see verbose per-flag descriptions; `--help` itself
stays under 50 lines for scannability.

The headline thesis: most "the model can't code" complaints are
really "the scaffold is too rich for this model". Shrew exists to
make a 9B–35B parameter local model feel like a competent coding
collaborator by tightening the harness around it.

This page walks you from zero to a working session in five minutes.
For deeper dives:

- [`small-model-setup.md`](small-model-setup.md) — llama.cpp build,
  GGUF download, MoE serving incantations.
- [`skills.md`](skills.md) — what the bundled skill markdown set is
  and how to extend it.
- [`extensions.md`](extensions.md) — `moe_offload`, `scaffold_fit`,
  and `tool_filter` — the three small-model adjustments shrew layers
  on top of weasel.
- [`benchmarks.md`](benchmarks.md) — Aider Polyglot + GAIA setup and
  evaluation.
- [`parity-matrix.md`](parity-matrix.md) — surface-by-surface parity
  status against the upstream small-model coding agent.
- [`security-and-trademarks.md`](security-and-trademarks.md) —
  trademark hygiene policy and the security posture.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- One of:
  - A running **llama.cpp** HTTP server on `127.0.0.1:8888`
    (recommended; see [`small-model-setup.md`](small-model-setup.md)).
  - A running **Ollama** daemon on `localhost:11434`.
  - A cloud provider key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or
    `OPENROUTER_API_KEY`) — used as a fallback when no local server is
    reachable.

```bash
uv --version                          # >= 0.4
uv sync --extra dev                   # core only
```

Cloud SDK extras (`--extra anthropic` / `--extra openai`) are only
required when you intend to fall back to a hosted model.

## Provider configuration

Shrew **inverts** the priority order weasel uses. A reachable local
server is the default; cloud providers are the fallback. The full
chain (first match wins):

1. `--model <id>` on the CLI.
2. `$SHREW_MODEL` environment variable.
3. **llama.cpp** at `$LLAMACPP_BASE_URL`
   (default `http://127.0.0.1:8888/v1`) — probed via `/health` then
   `/v1/models`. Default model: `qwen3.6-35b-a3b`.
4. **Ollama** at `$OLLAMA_BASE_URL` (default `http://localhost:11434`)
   — probed via `/api/tags`. Default model: `qwen3.5:cloud`.
5. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
6. `$OPENAI_API_KEY` set → defaults to `gpt-4o`.
7. `$OPENROUTER_API_KEY` set → defaults to `openai/gpt-4o`.
8. Friendly error pointing at every supported source.

The local-first ordering is deliberate: shrew exists to prove that
small local models are good enough for real coding work; reaching
for a cloud key should be a last resort, not the default.

## First one-shot turn

The simplest entry point — `-p` runs a single turn and exits:

```bash
chimera shrew -p "list the top-level files and read the README"
```

Expected output shape:

```text
I'll list the repo first, then read the README.

▶ list_files(path=".")
CHANGELOG.md  CLAUDE.md  README.md  chimera/  docs/  examples/  tests/

▶ Read(path="README.md")
# Chimera
A composable coding agent framework
...

The repo root has a README pitching Chimera as a composable coding agent framework.

[shrew] run saved as shrew-20260430T141802-2c8f9a3b at /Users/.../.chimera/eventlog/shrew-20260430T141802-2c8f9a3b/
```

Streaming text appears as it arrives. Tool calls render as
`▶ <Tool>(<args>)` lines. The trailing `[shrew] run saved as ...`
line on stderr points at the persisted run directory under
`~/.chimera/eventlog/`.

## The three flags that matter most

Shrew inherits weasel's full flag surface, but three flags are
disproportionately useful for small-model work:

### `--model`

Pin the model identifier. Shrew's default is `qwen3.6-35b-a3b` (a
Qwen MoE checkpoint served by llama.cpp). Override examples:

```bash
chimera shrew --model qwen3.5-9b -p "..."           # dense 9B local
chimera shrew --model qwen3.5:cloud -p "..."        # Ollama cloud tag
chimera shrew --model anthropic/claude-haiku-4-5 -p "..."   # cloud
chimera shrew --model openai/gpt-4o-mini -p "..."   # cloud
```

For the full list of recognised local ids, run
`chimera shrew --list-models`.

### `--vram-gb`

Tell shrew how much GPU VRAM you have. Shrew uses this to pick a
safe context window via the
[`moe_offload`](extensions.md#moe-offload) extension. Default: `8`
(the laptop-class target). Bigger values unlock larger context
windows; the helper snaps to a power of two and clamps at the
model's architectural maximum.

```bash
chimera shrew --vram-gb 24 -p "audit the repo"      # workstation GPU
chimera shrew --vram-gb 6  -p "audit the repo"      # tight laptop
```

You can also set it via `$SHREW_VRAM_GB` so CI and one-shot scripts
inherit the budget.

### `--no-skills`

Skip the bundled skill set. Skills layer in extra system-prompt
context (the curated knowledge / protocols / tools markdowns under
`chimera/shrew/skills/`). They help small models a lot, but they do
cost tokens. When you're benchmarking or running a frontier model
that doesn't need the scaffolding, drop them:

```bash
chimera shrew --no-skills --model gpt-4o -p "..."
```

The skill set is documented in [`skills.md`](skills.md).

## Other useful flags

```bash
chimera shrew --max-steps 20 -p "..."                  # cap turns
chimera shrew --allowed-tools Read,Bash -p "audit"     # tool allowlist
chimera shrew --allowed-tools= -p "..."                # full tool group
chimera shrew --json -p "ship it"                      # single JSON blob
chimera shrew --no-color -p "..." | tee shrew.log      # plain text
chimera shrew --list-models                            # known model ids
```

The defaults shrew pins on top of weasel:

| Flag | Shrew default | Why |
|---|---|---|
| `--model` | `qwen3.6-35b-a3b` | Local MoE, runs on a 32-64 GB Mac at 4-bit quant. |
| `--max-steps` | `30` | Smaller than mink/otter's `50` — small models loop on long horizons. |
| `--allowed-tools` | `Read,Write,Edit,Bash` | Minimal high-leverage toolkit; small models choke on big tool menus. |

## Drop into the REPL

Run `chimera shrew` with no `-p` flag for an interactive REPL:

```bash
chimera shrew
chimera shrew --model qwen3.5-9b
```

The REPL streams assistant text + tool calls inline, accepts
mid-turn steering, supports `Ctrl-C` cancellation, and exposes the
standard Chimera slash-command palette. Type `/help` at the prompt
for the live list.

Each REPL session is event-sourced under
`~/.chimera/eventlog/shrew-<utc>-<uuid>/`. To resume:

```bash
chimera shrew sessions list
chimera shrew sessions show shrew-20260430T141802-2c8f9a3b
```

## Run a benchmark

Smoke-test the wiring against Aider Polyglot or GAIA:

```bash
chimera shrew bench aider-polyglot --bench-limit 5
chimera shrew bench gaia --bench-limit 5
```

When the dataset isn't staged yet, shrew prints a setup hint and
exits with code `3`. See [`benchmarks.md`](benchmarks.md) for the
schema and staging steps.

## Env vars at a glance

| Variable | Default | Meaning |
|---|---|---|
| `SHREW_MODEL` | (unset) | Default model id when `--model` is not passed. |
| `SHREW_VRAM_GB` | `8` | VRAM budget passed to `moe_offload`. |
| `LLAMACPP_BASE_URL` | `http://127.0.0.1:8888/v1` | llama.cpp HTTP base. |
| `LLAMACPP_API_KEY` | (unset) | Optional auth header for llama.cpp. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama daemon base. |
| `OLLAMA_API_KEY` | (unset) | Optional auth for the Ollama OpenAI shim. |
| `ANTHROPIC_API_KEY` | (unset) | Activates Anthropic fallback. |
| `OPENAI_API_KEY` | (unset) | Activates OpenAI fallback. |
| `OPENROUTER_API_KEY` | (unset) | Activates OpenRouter fallback. |
| `CHIMERA_AIDER_POLYGLOT_PATH` | `~/.chimera/datasets/aider-polyglot` | Override polyglot dataset root. |
| `CHIMERA_GAIA_PATH` | `~/.chimera/datasets/gaia` | Override GAIA dataset root. |
| `NO_COLOR` | (unset) | Force the plain output handler. |

## What gets written to disk

| Path | What |
|---|---|
| `~/.chimera/eventlog/shrew-<id>/summary.json` | Per-run metadata. |
| `~/.chimera/eventlog/shrew-<id>/event-*.json` | Full event stream. |
| `~/.chimera/datasets/aider-polyglot/` | Default Aider Polyglot root. |
| `~/.chimera/datasets/gaia/` | Default GAIA root. |
| `~/.shrew/skills/` | Optional user-owned skill overlay. |

Everything is local and plaintext. To purge old runs:

```bash
rm -rf ~/.chimera/eventlog/shrew-*
```

## Where to go next

- Don't have llama.cpp running yet? Start with
  [`small-model-setup.md`](small-model-setup.md).
- Curious about the bundled skill set?
  [`skills.md`](skills.md).
- Want to tune (or disable) the small-model adjustments?
  [`extensions.md`](extensions.md).
- Ready to evaluate? [`benchmarks.md`](benchmarks.md).
- Need the surface-by-surface parity status?
  [`parity-matrix.md`](parity-matrix.md).
- Filing an issue? Read
  [`security-and-trademarks.md`](security-and-trademarks.md) first.

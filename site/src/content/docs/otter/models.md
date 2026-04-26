---
title: Otter Models
description: Model id catalogue, default model selection, and the OTTER_MODEL environment variable for chimera otter.
---

# Otter Models

`chimera otter` accepts any model id understood by Chimera's provider
factory (`chimera/providers/factory.py`). The provider chain auto-detects
the right backend from the prefix (`claude-*` → Anthropic, `gpt-*` /
`o1*` / `o3*` → OpenAI, `gemini-*` → Google, `glm-*` / `kimi-*` /
`qwen*` / `llama*` / `mistral*` / `phi*` → Ollama, `vendor/name` →
OpenRouter when the OpenRouter key is set). Otter does not maintain a
parallel registry; what works for `chimera mink` works here.

This page is the otter-flavored entry point for picking a model:

- The default the resolver lands on when you don't pass `--model`.
- The `OTTER_MODEL` environment variable for pinning a model in CI / a
  user's shell.
- Concrete ids that are tested today.

For the smoke-tested matrix of Ollama tags with measured cold-start, tool
turn time, and per-turn cost, see [`docs/mink/models.md`](../mink/models.md);
those numbers transfer to otter unchanged.

## Default model

Otter's resolver picks a default in this order
(`chimera/otter/providers.py:_resolve_model`):

1. `--model <id>` on the CLI (highest precedence).
2. `$OTTER_MODEL` environment variable.
3. If `$ANTHROPIC_API_KEY` is set: `claude-sonnet-4-6`.
4. If `$OPENROUTER_API_KEY` is set: `anthropic/claude-sonnet-4`.
5. If `$OPENAI_API_KEY` is set: `gpt-4o`.
6. Otherwise: a friendly error pointing at the three env vars above.

So a fresh laptop with only `$ANTHROPIC_API_KEY` exported gets
`claude-sonnet-4-6` automatically. A CI runner with
`$OPENROUTER_API_KEY` falls back to `anthropic/claude-sonnet-4` (routed
through the OpenAI-compatible adapter). A purely-OpenAI environment
lands on `gpt-4o`.

## The `OTTER_MODEL` env var

`OTTER_MODEL` is the project's escape hatch for "I want this model every
time, but I don't want to pass `--model`." It sits between the explicit
`--model` flag and the provider-specific defaults:

```bash
export OTTER_MODEL=claude-sonnet-4-6
chimera otter -p "summarize this repo"                  # uses claude-sonnet-4-6
chimera otter --model gpt-4o -p "draft a release note"  # overrides to gpt-4o
```

A few practical patterns:

```bash
# CI pins the cheapest first-class model.
export OTTER_MODEL=anthropic/claude-haiku-4
export OPENROUTER_API_KEY=sk-or-...

# Local dev pins a long-context model for refactor sessions.
export OTTER_MODEL=kimi-k2.6:cloud

# Privacy-sensitive shell pins a local Ollama tag.
export OTTER_MODEL=qwen3:32b
```

Compared to mink: mink has both `$CHIMERA_MINK_MODEL` and
`$CHIMERA_MINK_FALLBACK` (the second is used when the primary tag is
missing locally). Otter ships only `$OTTER_MODEL` — the fallback story
is handled by the resolver chain (Anthropic → OpenRouter → OpenAI), and
unknown Ollama tags surface as a provider error rather than silently
swapping.

## Recommended models

The list below names ids that have been smoke-tested against `chimera otter`
on at least one developer machine. Absence from this list does not mean
"unsupported"; it just means we haven't measured it.

### Anthropic

| Model id | Notes |
|---|---|
| `claude-sonnet-4-6` | **Otter default.** Strongest tool calling in the Anthropic line; supports extended thinking, prompt caching, vision. |
| `claude-opus-4` | Strongest reasoning; 2x cost of sonnet. Good for long-form refactors. |
| `claude-haiku-4` | Cheapest first-class Anthropic model; fine for short turns and quick edits. |
| `claude-sonnet-3-7` | Older release; useful when reproducing historical benchmark runs. |

### OpenAI

| Model id | Notes |
|---|---|
| `gpt-4o` | OpenAI default in the otter resolver. Streaming, tool calls, vision, JSON mode. |
| `gpt-4o-mini` | Cheaper; suitable for one-shots and CI smoke tests. |
| `o3` | Reasoning-tuned; spends more on reasoning tokens. The provider tracks them via `chimera/providers/cost_tracker.py`. |
| `o3-mini` | Smaller reasoning model; fast for proofs / structured tasks. |

### OpenRouter (`vendor/name`)

OpenRouter is keyed off the `/` in the model id. Anything with a slash,
when `$OPENROUTER_API_KEY` is set, routes through the OpenAI-compatible
adapter at `https://openrouter.ai/api/v1`.

| Model id | Notes |
|---|---|
| `anthropic/claude-sonnet-4` | OpenRouter default in the otter resolver. |
| `anthropic/claude-opus-4` | Same family, stronger reasoning. |
| `google/gemini-2.5-pro` | Long context, strong code-gen. |
| `meta-llama/llama-3.3-70b` | Open-weight; cheapest of the three. |
| `qwen/qwen-2.5-coder-32b` | Open-weight code model; fast on OpenRouter. |
| `deepseek/deepseek-chat` | Inexpensive general model with tool calls. |

### Ollama (local + cloud tags)

| Model id | Kind | Context | Notes |
|---|---|---|---|
| `glm-5.1:cloud` | cloud | 131072 | Validated working baseline; native tool calls. |
| `glm-5:cloud` | cloud | 131072 | Fast text response, dispatches tools cleanly. |
| `kimi-k2.6:cloud` | cloud | 262144 | Reasoning model (`think:true` auto-enabled). Long context. |
| `kimi-k2.5:cloud` | cloud | 131072 | Predecessor; steady tool use. |
| `minimax-m2.7:cloud` | cloud | 131072 | Newer minimax weights. |
| `qwen3.5:cloud` | cloud | 131072 | Fast, clean tool dispatch. |
| `gpt-oss:120b-cloud` | cloud | 131072 | Open-weight 120B; fast tool turn. |
| `qwen3:32b` | local | 131072 | Local fallback; runs on a 24 GB GPU. |
| `llama3.1:70b-instruct` | local | 131072 | Heavier local; good on 2× GPU rigs. |

For per-tag cold-start, tool dispatch wall-clock, and per-turn cost (all
measured against a real Ollama daemon) see
[`docs/mink/models.md`](../mink/models.md).

### Google

| Model id | Notes |
|---|---|
| `gemini-2.5-pro` | 1M context, strong code-gen; non-streaming via the chimera adapter. |
| `gemini-2.0-flash` | Fastest Gemini; good for cheap one-shots. |

### Anthropic-compatible third-party endpoints

Set `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` and the Anthropic
provider talks to whatever endpoint you point it at:

| Endpoint | Model ids |
|---|---|
| `api.z.ai/v1/anthropic` (GLM) | `glm-4.6`, `glm-4.5` |
| Moonshot Anthropic-compat | `kimi-k2-instruct` |
| In-house gateway | whatever your gateway exposes |

## Selecting a model

Three layered ways to pick a model — last write wins:

1. **CLI flag (highest):** `chimera otter --model <id>`.
2. **Environment:** `export OTTER_MODEL=<id>` in the shell or CI.
3. **Default chain:** the first env var that's set among
   `$ANTHROPIC_API_KEY` / `$OPENROUTER_API_KEY` / `$OPENAI_API_KEY`.

Examples:

```bash
# One-off override.
chimera otter --model claude-opus-4 -p "review this PR"

# Pin for the shell session.
export OTTER_MODEL=anthropic/claude-sonnet-4
export OPENROUTER_API_KEY=sk-or-...
chimera otter -p "summarize"

# Resolver default (no flag, no env, but $ANTHROPIC_API_KEY set).
export ANTHROPIC_API_KEY=sk-ant-...
chimera otter -p "explain this repo"   # → claude-sonnet-4-6
```

## REPL `/model` slash command

The otter REPL exposes a `/model` slash command. With a single-model
launch it prints the active model. With `--models a,b,c` it cycles
through the comma-separated list:

```bash
chimera otter --models claude-sonnet-4-6,gpt-4o,gemini-2.5-pro
# inside the REPL:
/model            # show current
/model next       # cycle to gpt-4o
/model prev       # cycle back
```

Each cycle rebuilds the provider behind the live session; sessions
event-log records the model on each turn so [`sessions show`](sessions.md)
can show you which model produced which event.

## Adding your own model

Three paths, depending on how the model is served:

1. **Custom Ollama tag.** Build a `Modelfile` in front of a base tag,
   `ollama create my-agent -f ./Modelfile`, then
   `chimera otter --model my-agent -p "..."`. The provider hits
   `/api/chat` like any other Ollama tag.
2. **Custom OpenAI-compatible endpoint.** Set `OPENAI_BASE_URL` (or pass
   `base_url=` when you build the provider directly) and use a model id
   the endpoint understands. Otter routes through the `compatible`
   adapter when the prefix doesn't match a built-in.
3. **Brand-new provider.** Implement `Provider`
   (`chimera/providers/base.py`) and call `register_provider`
   (`chimera/providers/registry.py`). See the [`providers.md`](providers.md)
   "Custom providers" section for the factory contract.

## Cost accounting

Otter writes `cost_usd` to each run's `summary.json`
(`chimera/otter/cli.py:_write_run_summary`). The number comes from
`chimera/providers/cost.py` (per-model pricing) and the cost tracker
(`chimera/providers/cost_tracker.py`). Models without an entry in the
pricing table report `0.0` — that's not free, it's "we don't know what
the upstream charges." Hosted models served by the local Ollama daemon
also land at `0.0` because `/api/chat` does not surface a price field.

To audit your spend across persisted runs:

```bash
chimera otter sessions list --json | jq '[.[].cost_usd] | add'
```

A roll-up subcommand similar to `chimera mink runs cost` is on the
roadmap.

## See also

- [`providers.md`](providers.md) — provider chain that picks the SDK to
  drive a given model id.
- [`docs/mink/models.md`](../mink/models.md) — measured smoke matrix for
  every Ollama tag, with run ids and cost data.
- [`quickstart.md`](quickstart.md) — first-call walkthrough.
- [`sessions.md`](sessions.md) — where the model id lands in
  `summary.json`.

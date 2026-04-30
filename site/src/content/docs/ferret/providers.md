---
title: Ferret Providers
description: Provider chain for chimera ferret — OpenAI-flagship default, Anthropic, OpenRouter, and Ollama fallbacks.
---

# Providers — what ferret can talk to

`chimera ferret` reuses Chimera's standard provider stack
(`chimera/providers/factory.py`), so any provider that mink or otter
can drive, ferret can drive. The difference is in defaults: ferret's
resolver (`chimera/ferret/providers.py`) prefers an OpenAI-flagship
chain — OpenAI first, then Anthropic, then OpenRouter — because the
upstream IDE-first OpenAI-flagship coding agent it parallels is
overwhelmingly run against the OpenAI hosted models.

This page is the ferret-specific layer on top of the provider matrix.
For the deep, line-numbered tour of every adapter (Ollama internals,
Anthropic streaming, OpenAI delta accumulation, etc.) see
[`docs/mink/providers.md`](../mink/providers.md). The same code is in
play.

## Resolution order

`build_provider(args)` in `chimera/ferret/providers.py` walks this
chain on every ferret invocation. First match wins.

1. Explicit `args.model` (CLI `--model <id>`).
2. `$FERRET_MODEL` environment variable.
3. `$OPENAI_API_KEY` set → defaults to `gpt-5`. If the GPT-5 family
   is not yet enabled on your account, falls through to `gpt-4o`.
4. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
5. `$OPENROUTER_API_KEY` set → defaults to `openai/gpt-5`, routed
   through the OpenAI-compatible adapter against `openrouter.ai`.
6. Friendly error pointing at the four env vars above.

Explicit beats env beats default, so
`chimera ferret --model claude-sonnet-4-6 -p "..."` works even when
`$OPENAI_API_KEY` is set.

## OpenAI (default)

The first-class ferret target. `gpt-5` is the default because it's
the model the upstream parallel ships against and ferret's sandbox
+ approval defaults are tuned around its tool-use behavior.

- **Setup:**
  ```bash
  uv sync --extra openai
  export OPENAI_API_KEY=sk-...
  ```
- **Use with ferret:**
  ```bash
  chimera ferret -p "review this PR"
  chimera ferret --model gpt-4o -p "long-form refactor"
  chimera ferret --model o3-mini -p "prove this invariant"
  ```
- **What's wired:** streaming, tool calls, async, reasoning effort
  (`--thinking low|medium|high|max`), vision. See
  `chimera/providers/openai_provider.py`.
- **OpenAI-compatible endpoints:** the same provider also accepts
  `OPENAI_BASE_URL`, so you can route ferret through any third-party
  gateway that speaks the OpenAI Chat Completions API:
  ```bash
  export OPENAI_BASE_URL=https://gateway.example.com/v1
  chimera ferret --model gpt-4o -p "..."
  ```

## Anthropic

Use when you have an Anthropic key and want `claude-sonnet-4-6` or
`claude-opus-4`. Ferret will pick Anthropic automatically when
`$OPENAI_API_KEY` is unset.

- **Setup:**
  ```bash
  uv sync --extra anthropic
  export ANTHROPIC_API_KEY=sk-ant-...
  ```
- **Use with ferret:**
  ```bash
  chimera ferret --model claude-sonnet-4-6 -p "draft a release note"
  chimera ferret --model claude-opus-4 -p "long-context analysis"
  ```
- **What's wired:** streaming, tool calls, async, extended thinking,
  prompt caching, vision. See `chimera/providers/anthropic.py`.
- **Anthropic-compatible endpoints:** the same provider also accepts
  `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`, so you can route
  ferret through GLM-4.6 (`api.z.ai`), Moonshot, or any third-party
  gateway that speaks the Messages API.

## OpenRouter

Use when you want a single API key that covers multiple providers,
or when you specifically want to A/B a model that's not yet first-
class in Chimera. The OpenRouter adapter rides on the
OpenAI-compatible adapter against `openrouter.ai`.

- **Setup:**
  ```bash
  export OPENROUTER_API_KEY=sk-or-...
  ```
- **Use with ferret:**
  ```bash
  chimera ferret --model openai/gpt-5 -p "..."
  chimera ferret --model anthropic/claude-sonnet-4 -p "..."
  chimera ferret --model meta-llama/llama-3.3-70b-instruct -p "..."
  ```
- The model id passed to `--model` is forwarded verbatim; ferret does
  not maintain a private allowlist of OpenRouter slugs.

## Ollama (local)

Use when you want everything to stay on-device — no API key, no
network egress, no per-token cost. Ferret detects an Ollama daemon
the same way otter does: a `:tag` in the model id (`qwen3:32b`,
`llama3.3:70b`, etc.) routes through the Ollama adapter.

- **Setup:**
  ```bash
  ollama serve &
  ollama pull qwen3:32b
  ```
- **Use with ferret:**
  ```bash
  chimera ferret --model qwen3:32b -p "explain this repo"
  chimera ferret --model llama3.3:70b -p "fix the failing test"
  ```
- **What's wired:** streaming, tool calls (when the local model
  supports them), async. See `chimera/providers/ollama.py`.
- **Sandbox interaction:** local models pair well with
  `--sandbox workspace-write`, since you don't pay per-token for
  long agentic loops. See [`sandbox.md`](sandbox.md).

## Custom registrations

Ferret inherits Chimera's runtime provider registry (see
`chimera/providers/registry.py`). You can register a custom provider
factory at import time and use it with `--model your-prefix/model`:

```python
from chimera.providers.registry import register_provider
from chimera.providers.compatible import OpenAICompatibleProvider

def _factory(model: str, **kw):
    return OpenAICompatibleProvider(
        api_key=os.environ["EXAMPLE_API_KEY"],
        base_url="https://api.example.com/v1",
        model=model.split("/", 1)[1],
        **kw,
    )

register_provider(prefix="example", factory=_factory)
```

Then:

```bash
chimera ferret --model example/our-flagship -p "..."
```

## Picking a provider for a task

A rough rubric (your mileage will vary by workload):

| Task | Suggested model |
|---|---|
| Short edits, tight loop | `gpt-5` (default) or `gpt-4o` |
| Long-context refactor / code review | `gpt-5` or `claude-opus-4` |
| Reasoning-heavy proof / synthesis | `o3` / `o3-mini` |
| Cheap-and-fast scratch work | `gpt-4o-mini`, `claude-haiku-4` |
| Offline / privacy-sensitive | `qwen3:32b`, `llama3.3:70b` (Ollama) |

## Cost tracking

Ferret's per-step cost tracker (`chimera/providers/cost_tracker.py`)
records token usage including cache hits and reasoning tokens for
every step. The REPL `/cost` slash command prints a running total;
the eventlog `summary.json` records the final figure. Custom model
pricing (for Ollama, OpenRouter slugs, or anything outside the
built-in catalog) can be registered through
`chimera/providers/cost.py`.

## See also

- [`quickstart.md`](quickstart.md) — first-run walk-through.
- [`sandbox.md`](sandbox.md) — sandbox flag interaction with provider
  network access.
- [`../otter/providers.md`](../otter/providers.md) — sibling provider
  chain (Anthropic-first).
- [`../mink/providers.md`](../mink/providers.md) — line-numbered tour
  of every adapter.

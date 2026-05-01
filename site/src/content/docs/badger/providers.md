---
title: Badger Providers
description: Anthropic-first, general-purpose provider chain with sane fallbacks.
---

# Providers

Badger ships an **Anthropic-first, general-purpose** provider chain.
The harness-rewrite tradition was first articulated against Anthropic
models, so badger keeps that as the default.

## Resolution chain

First match wins:

1. Explicit `--model`.
2. `$BADGER_MODEL` env var.
3. `$ANTHROPIC_API_KEY` set → default `claude-sonnet-4-6`.
4. `$OPENAI_API_KEY` set → default `gpt-4o`.
5. `$OPENROUTER_API_KEY` set → default `anthropic/claude-sonnet-4-6`.
6. `$OLLAMA_HOST` set → default `qwen3:32b`.
7. Friendly `ValueError` with a list of the env vars above.

## Examples

```bash
# Anthropic — the badger default
export ANTHROPIC_API_KEY=sk-ant-...
chimera badger -p "Implement the resolver"

# OpenAI
export OPENAI_API_KEY=sk-...
chimera badger -p "Implement the resolver"

# OpenRouter (vendor/name routing)
export OPENROUTER_API_KEY=sk-or-...
chimera badger --model openai/gpt-4o -p "..."

# Local Ollama
export OLLAMA_HOST=http://localhost:11434
chimera badger --model qwen3:32b -p "..."
```

## Routing rules

- **OpenRouter**: a model id with a `/` plus `$OPENROUTER_API_KEY` →
  routed via `chimera/providers/compatible.py` against
  `https://openrouter.ai/api/v1`.
- **Ollama**: a model id with a `:` (e.g. `qwen3:32b`,
  `kimi-k2.6:cloud`) routes through `OllamaProvider`. The base URL
  defaults to `$OLLAMA_HOST` or `http://localhost:11434`.
- **Otherwise**: the generic `chimera.providers.factory.create_provider`
  picks Anthropic / OpenAI / Google / xAI by model prefix.

## Cosmetic OpenRouter headers

Badger ships sensible defaults for OpenRouter's two cosmetic headers
(`HTTP-Referer`, `X-Title`). They identify the client in the
OpenRouter dashboard:

| Variable             | Default                                     |
| -------------------- | ------------------------------------------- |
| `OPENROUTER_REFERER` | `https://github.com/0bserver07/chimera`     |
| `OPENROUTER_TITLE`   | `chimera badger 0.5.0`                      |

Override either via env var.

## Tests

`tests/badger/test_providers.py` covers the resolution chain in
isolation (no live API calls). Mark tests for live providers as
`@pytest.mark.live` so the default suite stays hermetic.

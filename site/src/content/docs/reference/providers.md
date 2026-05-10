---
title: "chimera.providers"
description: "Reference for chimera.providers — Provider ABC, factory, registry, catalog, thinking levels."
---

`chimera.providers` is the LLM-backend layer. Every provider implements
the `Provider` ABC; `create_provider()` auto-detects the right one from
a model name or explicit `provider_type`.

## Top-level exports

```python
from chimera.providers import (
    Provider,
    Response,
    StreamEvent,
    create_provider,
    register_provider,
    get_provider_factory,
    list_providers,
    ThinkingLevel,
    budget_for_level,
    ProxyProvider,
)
from chimera.providers.catalog import ModelConfig, ProviderCatalog
```

| Symbol | Purpose |
|---|---|
| `Provider` | ABC. Implement `complete(messages, tools=...)` (sync) and/or `stream(...)`. Accepts a `thinking` parameter. |
| `Response` | Dataclass with `content`, `tool_calls`, `usage`, `cost`, `stop_reason`. |
| `StreamEvent` | Streaming delta type yielded by `Provider.stream`. |
| `create_provider(provider_type=None, model=None, **kwargs)` | Factory with model-name autodetection (see below). |
| `register_provider(name, factory)` | Register a custom provider type at runtime. |
| `get_provider_factory(name)` / `list_providers()` | Inspect the registry. |
| `ThinkingLevel` | Enum: `OFF` / `MINIMAL` / `LOW` / `MEDIUM` / `HIGH` / `MAX`. |
| `budget_for_level(level)` | Map a `ThinkingLevel` to a token budget. |
| `ProxyProvider` | Wrap another provider to intercept/log/transform requests. |
| `ModelConfig` / `ProviderCatalog` | Catalog API for slash-namespaced models (`bedrock/claude-sonnet-4`, `azure/gpt-4o`, `groq/llama-3.3-70b`, ...). |

## Built-in provider modules

| Module | Class | Routes by name prefix |
|---|---|---|
| `chimera.providers.anthropic` | `AnthropicProvider` | `claude-*` |
| `chimera.providers.openai_provider` | `OpenAIProvider` | `gpt-*`, `o1-*`, `o3-*` |
| `chimera.providers.google` | `GoogleProvider` | `gemini-*` |
| `chimera.providers.ollama` | `OllamaProvider` | `llama*`, `mistral*`, `qwen*`, `phi*` |
| `chimera.providers.modal` | `ModalProvider` | (explicit) |
| `chimera.providers.compatible` | `CompatibleProvider` | OpenAI-compatible endpoints (vLLM, LiteLLM, Together, Groq, Fireworks). Requires `base_url`. |
| `chimera.providers.proxy` | `ProxyProvider` | Wraps any other provider. |

## `create_provider()` resolution order

1. Explicit `provider_type` (e.g. `"anthropic"`, `"compatible"`).
2. Known model-name prefix (the table above).
3. `ProviderCatalog.default()` lookup (slash-namespaced ids).
4. Environment-variable fallback (`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` → Anthropic; `OPENAI_API_KEY` → OpenAI).

For the full list of catalog ids and their env-var contracts (DeepSeek,
GLM, Kimi, Qwen3, GPT-OSS, Mistral Codestral, Gemma 3, Bedrock, Azure,
Groq), see [Use with Third-Party Providers](/use-with-third-party-providers/).

## Cost tracking

```python
from chimera.providers.cost import register_model_cost
from chimera.providers.cost_tracker import CostTracker
```

`register_model_cost(model, input_per_mtok, output_per_mtok)` plugs a
custom price into the global table; `CostTracker` records granular
per-step token counts (cache hits, reasoning tokens, regular input /
output) and rolls them up into a session total.

## See also

- [Use with Third-Party Providers](/use-with-third-party-providers/) for
  end-to-end env-var setups.
- [modules/provider-registry](/modules/provider-registry/) for the
  registry's behaviour with plugin-supplied providers.
- [`chimera.providers.thinking`](/modules/thinking-levels/) for the
  extended-thinking budget table.

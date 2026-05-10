---
title: "Provider Registry"
description: "Provider Registry"
---

`chimera.providers.registry` is a module-level dictionary that maps provider
name strings to factory callables.  Built-in providers self-register at import
time; plugins use the same API to add custom providers.

## API

| Function | Description |
|----------|-------------|
| `register_provider(name, factory)` | Register a factory under `name`; overwrites any existing entry |
| `get_provider_factory(name)` | Return the factory for `name`, or `None` if not registered |
| `list_providers()` | Return all registered provider names as a list of strings |
| `unregister_provider(name)` | Remove a provider; no-op if not registered |
| `_ensure_builtins_registered()` | Import all built-in provider modules to trigger self-registration (called by `create_provider`) |

`ProviderFactory` is a type alias for `Callable[..., Provider]`.  Factories
receive keyword arguments (`model`, `api_key`, `base_url`, etc.) and return a
`Provider` instance.

## Self-registration pattern

Each built-in provider module registers itself at the bottom of the file:

```python
# chimera/providers/anthropic.py (simplified)
from chimera.providers.registry import register_provider

def _anthropic_factory(model: str = "", api_key: str | None = None, **kw):
    return AnthropicProvider(model=model, api_key=api_key)

register_provider("anthropic", _anthropic_factory)
```

`_ensure_builtins_registered()` imports all seven built-in modules
(`anthropic`, `openai`, `google`, `ollama`, `compatible`, `modal`,
`xai`) exactly once.

## Registering a custom provider

```python
from chimera.providers.registry import register_provider
from chimera.providers.base import Provider, Response

class MyProvider(Provider):
    def complete(self, messages, **kw) -> Response: ...
    # ... implement abstract methods

def _my_factory(model: str = "", **kw) -> MyProvider:
    return MyProvider(model=model)

register_provider("my-provider", _my_factory)

# Now usable via create_provider:
from chimera.providers.factory import create_provider
provider = create_provider("my-provider", model="my-model-v1")
```

## Built-in catalog

`chimera.providers.catalog._BUILTIN_ENTRIES` ships a `ModelConfig` for
every model id Chimera knows out-of-the-box. `create_provider()` falls
through to the catalog when prefix-based inference doesn't find a
match. Pricing is mirrored in
[`chimera.providers.cost.PRICING`](/modules/cost-tracking/) so
`CostTracker.record_usage()` resolves the right rate.

### Catalog refresh — Wave 12 + 13 (13 new model entries)

Wave 12 ([`W12-1-DEEPSEEK-V4`](https://github.com/0bserver07/chimera/blob/master/research/W12-1-DEEPSEEK-V4.md))
added the DeepSeek-V4 family. Wave 13
([`W13-E2-CATALOG`](https://github.com/0bserver07/chimera/blob/master/research/W13-E2-CATALOG.md))
swept seven more vendors. All entries below ship with documented
endpoints, env-var bindings, context windows, and pricing tuples;
unknown-model errors now mention every new prefix so misroutes
surface eagerly.

| Model id | Routes to | Endpoint / `base_url` | API-key env | Context | Cost ($/Mtok in/out) | Notes |
|---|---|---|---|---|---|---|
| `deepseek-v4` | `compatible` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` | 128k | 0.55 / 2.19 | Placeholder pricing; copies `deepseek-reasoner` until DeepSeek publishes V4 rates. |
| `deepseek-v4-pro` | `compatible` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` | 128k | 0.55 / 2.19 | Same family; pro tier. |
| `deepseek-v4-pro:cloud` | `ollama` | `$OLLAMA_HOST` | — | 262k | 0.55 / 2.19 | Local Ollama daemon's cloud passthrough (`ollama run deepseek-v4-pro:cloud`). |
| `deepseek-v3.1-terminus` | `compatible` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` | 128k | 0.27 / 1.10 | Hosted V3 line. |
| `deepseek-coder-v3` | `compatible` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` | 128k | 0.27 / 1.10 | Hosted V3 coder SKU. |
| `qwen3-coder` / `qwen3-coder-30b` / `qwen3-32b` | `ollama` | `$OLLAMA_HOST` | — | 131k | 0.0 / 0.0 | Local-only via the `qwen` prefix; DashScope users register a `compatible` factory explicitly. |
| `glm-4.6` | `anthropic` | `https://api.z.ai/api/anthropic` | `ANTHROPIC_AUTH_TOKEN` | 200k | 0.6 / 2.2 | Anthropic-compat wire protocol via api.z.ai. |
| `glm-5.1` | `anthropic` | `https://api.z.ai/api/anthropic` | `ANTHROPIC_AUTH_TOKEN` | 200k | 2.0 / 8.0 | Same gateway, GLM-5 tier. |
| `gpt-oss-120b` / `gpt-oss-20b` | `ollama` | `$OLLAMA_HOST` | — | 131k | 0.0 / 0.0 | OpenAI open-weights; `gpt-oss` prefix routes to ollama *before* the generic `gpt-` → openai branch. |
| `kimi-k2-0905-preview` | `anthropic` | `https://api.moonshot.ai/anthropic` | `MOONSHOT_API_KEY` | 200k | 0.6 / 2.5 | Moonshot Anthropic-compat. |
| `kimi-k2.5` | `anthropic` | `https://api.moonshot.ai/anthropic` | `MOONSHOT_API_KEY` | 200k | 0.6 / 2.5 | Same family, K2.5 line. |
| `mistral-codestral-2511` | `ollama` | `$OLLAMA_HOST` | — | 256k | 0.0 / 0.0 | Codestral coder line via the `mistral` prefix. |
| `gemma3-27b-instruct` | `ollama` | `$OLLAMA_HOST` | — | 131k | 0.0 / 0.0 | Google Gemma 3 open weights; `gemma` prefix added in W13-E2. |

Routing precedence:

1. Explicit factory passed to `create_provider()`.
2. Prefix match in `_infer_provider()` (`gpt-oss`, `gemma`, `qwen`,
   `mistral`, `phi`, `llama` → `ollama`; `gpt` → `openai`; `claude` /
   `glm` / `kimi` → `anthropic`; etc.).
3. `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` env-override (always
   wins for non-`gpt-oss` ids).
4. Catalog fallback through `ProviderCatalog.create()`.

Pricing entries flagged with TODO comments in `cost.py` are
placeholders — refresh once vendors publish per-SKU rates. Local
Ollama-served weights stay at `(0.0, 0.0)` because Ollama doesn't
surface a price field.

---
title: "Add a Provider in 20 Lines"
description: "Add a new LLM backend to Chimera as a declarative capability row keyed by wire protocol — no new Provider subclass."
---

There are two questions that sound the same but aren't:

- **"Point Chimera at a backend I can already reach."** Set a few env vars or
  register a catalog entry. Covered in
  [Use with Third-Party Providers](/use-with-third-party-providers/) and
  [Model Catalog](/model-catalog/).
- **"Teach Chimera a new backend that diverges in wire quirks."** This is the
  framework-author question. It used to mean writing a new `Provider`
  subclass. It no longer does.

Most backends do not have their own protocol — they share one. Every
OpenAI-compatible service (OpenRouter, Together, Groq, vLLM, DeepSeek's hosted
API, xAI) speaks the same `/v1/chat/completions` shape. Every
Anthropic-compatible service (Claude, GLM via api.z.ai, Kimi via Moonshot)
speaks the Messages API. What differs between them is a handful of knobs. So
Chimera keys those knobs off the **wire protocol** and stores the per-vendor
divergence as **data** in a capability matrix. Adding a backend on an existing
protocol is a ~20-line data row.

## Wire protocols, not brands

`chimera.providers.capabilities.WireProtocol` enumerates the request/response
shapes Chimera speaks:

| Protocol | Endpoint shape | Spoken by |
|---|---|---|
| `openai-compat` | `POST /v1/chat/completions` | OpenRouter, Together, Groq, vLLM, SGLang, DeepSeek API, xAI, Modal endpoints |
| `anthropic-compat` | Anthropic Messages API | Claude, GLM (api.z.ai), Kimi (Moonshot) |
| `google` | Gemini `generateContent` | Gemini |

## The capability matrix

`ProviderCapabilities` is a frozen dataclass of quirk knobs — the single
source of truth for how a request is shaped. A subset:

| Knob | Meaning |
|---|---|
| `max_tokens_field` | Request field naming the output cap (`max_tokens` vs `max_completion_tokens` vs `max_output_tokens`) |
| `supports_temperature` | Whether `temperature` may be sent (reasoning models reject it) |
| `extra_payload` | Backend-specific params merged into every request |
| `supports_strict_tools` | Emit `strict: true` on function tools |
| `thinking_format` | How extended reasoning is encoded (`anthropic-budget`, `openai-effort`, `none`) |
| `cache_style` | How reusable prefixes are marked (`anthropic-ephemeral`, `openai-automatic`, `none`) |
| `default_max_tokens` | Per-turn output cap when the caller passes none |
| `tiered_pricing`, `one_hour_cache_write_premium`, `supports_stop_sequences`, `accepts_extra_headers` | Declared metadata callers and cost logic read |

Capabilities resolve in three layers, most-specific wins:

```
protocol default  →  provider override  →  model-prefix override
```

```python
from chimera.providers.capabilities import WireProtocol, resolve_capabilities

# Protocol default:
caps = resolve_capabilities(WireProtocol.OPENAI_COMPAT)

# Model-prefix override (o1/o3/o4/gpt-5 want max_completion_tokens, no temperature):
caps = resolve_capabilities(WireProtocol.OPENAI_COMPAT, model="o3-mini")
assert caps.max_tokens_field == "max_completion_tokens"
assert caps.supports_temperature is False
```

`extra_payload` is merged additively across layers; every other field is
replaced by the more-specific layer.

## Add a provider in 20 lines

A new OpenAI-compatible backend is a base URL, a capability row, and a
registry lambda. This is the entire (fictional) `chimera/providers/acmecloud.py`:

```python
from chimera.providers.capabilities import WireProtocol, register_capabilities
from chimera.providers.compatible import OpenAICompatibleProvider
from chimera.providers.registry import register_provider

ACMECLOUD_BASE_URL = "https://api.acmecloud.example/v1"

# Divergence-as-data: ACME Cloud wants strict function tools and stamps a
# house reasoning knob into every request. Both are matrix values, not code.
register_capabilities(
    WireProtocol.OPENAI_COMPAT,
    provider="acmecloud",
    supports_strict_tools=True,
    extra_payload={"acmecloud_reasoning": "auto"},
)

register_provider(
    "acmecloud",
    lambda model="", api_key=None, base_url=None, **kw: OpenAICompatibleProvider(
        model=model, base_url=base_url or ACMECLOUD_BASE_URL, api_key=api_key,
        provider="acmecloud", **kw,
    ),
)
```

No new class. The shared `OpenAICompatibleProvider` handles the wire; the
`provider="acmecloud"` hint tells it which capability row to resolve. Now:

```python
from chimera.providers.factory import create_provider

p = create_provider(provider_type="acmecloud", model="acmecloud-fast", api_key="k")
p._capabilities.supports_strict_tools   # True — from the matrix
p._capabilities.extra_payload           # {"acmecloud_reasoning": "auto"}
p.complete([...])                       # POSTs to https://api.acmecloud.example/v1/chat/completions
```

Because the capabilities drive request shaping, every request ACME Cloud sends
carries `acmecloud_reasoning: "auto"` and marks its function tools
`strict: true` — purely because the data row said so.

To make a provider a built-in (importable without the caller importing the
module), add one line to `chimera/providers/registry.py`'s
`_ensure_builtins_registered`. Plugins register the same way from their own
`load()` — no core edit needed.

## Per-model overrides

When divergence is per-model rather than per-vendor, key it on a model
prefix. This is exactly how the reasoning-model and large-output quirks are
expressed today:

```python
from chimera.providers.capabilities import WireProtocol, register_capabilities

# GLM/Kimi/Qwen/DeepSeek served over Anthropic-compat support larger outputs
# than Claude, so they get a bigger default cap:
register_capabilities(
    WireProtocol.ANTHROPIC_COMPAT, model_prefix="glm", default_max_tokens=32_768,
)
```

Longest matching prefix wins, matching cost-table semantics.

## Zero-dependency core

The matrix (`capabilities.py`) is pure standard library — it imports nothing
from the rest of the provider stack, so it can seed provider construction
without import cycles and without pulling any SDK. Provider SDKs
(`anthropic`, `openai`, `google-generativeai`) stay optional extras; the
OpenAI-compatible path needs only `httpx`. A data row for a new
OpenAI-compatible backend adds **no** dependency.

## When you still write a subclass

The native-SDK providers (`anthropic.py`, `openai.py`, `google.py`) remain
classes because they wrap vendor SDKs with real streaming, cancellation, and
prompt-cache plumbing. But even they now source their per-model quirks from
the matrix — for example `AnthropicProvider._default_max_tokens` reads
`resolve_capabilities(WireProtocol.ANTHROPIC_COMPAT, model=...)` instead of a
hardcoded prefix set. Reach for a subclass only when the wire itself is new;
a new *vendor* on an existing wire is a data row.

## Next steps

- [Use with Third-Party Providers](/use-with-third-party-providers/) — the
  env-var / `create_provider()` side for backends you can already reach.
- [Model Catalog](/model-catalog/) — bind a model id to a base URL, key env
  var, context window, and price.
- [Build a Coding Agent](/build-a-coding-agent/) — drive any provider inside
  an agent loop.

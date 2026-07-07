---
title: "Prompt Caching"
description: "Turn on prompt caching with one provider-agnostic knob and stop re-billing the stable prefix every turn."
---

Agentic loops resend the whole conversation on every turn, so the same system
prompt and tool definitions get billed again and again. Chimera exposes a
single provider-agnostic `cache` knob that marks the reusable prefix so each
turn reuses the previous turn's cached tokens instead of paying full price.

---

## The `cache` knob

The convention is defined on the `Provider` base class in
`chimera/providers/base.py` as `CACHE_LEVELS`:

```python
CACHE_LEVELS = ("none", "short", "long")
```

| Value | Meaning |
|---|---|
| `"none"` | No caching. **The default — zero behavior change.** |
| `"short"` | 5-minute ephemeral cache of the reusable prompt prefix. |
| `"long"` | 1-hour cache where the backend supports an extended TTL; otherwise equivalent to `"short"`. |

This is a documented *convention*, not an abstract-method contract. The
`Provider` ABC declares no `__init__`, so each concrete provider accepts
`cache` in its own constructor and applies it when building the request. A
provider whose backend has no cache concept simply ignores the argument and
stays correct — it just pays full price.

---

## Turning it on

Pass `cache` to the provider constructor:

```python
from chimera.providers.anthropic import AnthropicProvider

provider = AnthropicProvider(model="glm-5.2", cache="short")
```

Or thread it through the `create_provider()` factory — extra keyword
arguments are forwarded to the provider constructor:

```python
import chimera

provider = chimera.create_provider(model="glm-5.2", cache="long")
```

An invalid value is rejected at construction time:

```python
AnthropicProvider(model="glm-5.2", cache="forever")
# ValueError: cache must be one of ('none', 'short', 'long'), got 'forever'
```

---

## What gets cached

With caching enabled, the Anthropic-compatible provider attaches one
`cache_control` marker (a uniform TTL, so there are no 5m/1h ordering
constraints) to three places in each request:

1. **The system prompt** — the stable instruction block.
2. **The last tool definition** — the tail of the tool schema array, which
   marks the whole tool block as cacheable.
3. **The last message** — the *rolling* breakpoint. Marking the final content
   block means each turn reuses the previous turn's cached prefix (system +
   tools + all prior messages) and only bills the new tail.

Under the hood, `"short"` emits `{"type": "ephemeral"}` and `"long"` emits
`{"type": "ephemeral", "ttl": "1h"}`.

---

## Reading cache usage

When the backend returns cache accounting, the provider surfaces it on the
response's `usage` dict:

```python
resp = provider.complete(messages)
print(resp.usage["input_tokens"])
print(resp.usage.get("cache_creation_input_tokens"))  # tokens written to cache
print(resp.usage.get("cache_read_input_tokens"))      # tokens served from cache
```

`cache_creation_input_tokens` counts tokens written into the cache on the
first call; `cache_read_input_tokens` counts tokens served from cache on
subsequent calls. Both keys are present on the streaming `done` event too.

:::note
Live-proven against the z.ai GLM endpoint: a second identical call returned
nearly all of its input tokens as `cache_read_input_tokens` (roughly
6720 of 6732), confirming the rolling prefix is being served from cache
rather than re-billed. Values above are read straight off the response
`usage` — they come back as zero when caching is disabled.
:::

---

## The `enable_cache` alias

`enable_cache=True` is a deprecated predecessor flag. It now aliases
`cache="short"`, so older code keeps working:

```python
# These two are equivalent:
AnthropicProvider(model="glm-5.2", enable_cache=True)
AnthropicProvider(model="glm-5.2", cache="short")
```

An explicit `cache` always wins, so `cache="long", enable_cache=True` stays
`"long"`. Prefer the `cache=` string in new code.

---

## Next Steps

- [Use with Third-Party Providers](/use-with-third-party-providers/) — point
  the Anthropic provider at the z.ai GLM endpoint.
- [Model Catalog](/model-catalog/) — see how input/output tokens turn into a
  dollar cost.

---
title: "chimera.providers"
description: "chimera.providers"
---

::: chimera.providers
    options:
      show_submodules: true

## New modules (pi-mono)

The following provider-layer modules were added as part of the pi-mono adoption:

| Module | Key exports | Description |
|--------|-------------|-------------|
| `registry.py` | `register_provider`, `get_provider_factory`, `list_providers` | Global provider registry; allows plugins to register custom providers by name for use with `create_provider()` |
| `proxy.py` | `ProxyProvider` | Wraps another provider to intercept, log, or transform requests and responses without subclassing |
| `thinking.py` | `ThinkingLevel`, `budget_for_level` | Enum (`none`, `low`, `medium`, `high`) and helper that maps a level to a token budget for extended thinking |

All built-in providers (`AnthropicProvider`, `OpenAIProvider`, `GoogleProvider`,
`OllamaProvider`, `ModalProvider`) now accept a `thinking` parameter that takes
a `ThinkingLevel` value.  When set, the provider enables extended/chain-of-thought
reasoning with the token budget returned by `budget_for_level`.

::: chimera.providers.registry

::: chimera.providers.proxy

::: chimera.providers.thinking

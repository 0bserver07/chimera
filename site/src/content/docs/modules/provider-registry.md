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

`_ensure_builtins_registered()` imports all six built-in modules
(`anthropic`, `openai`, `google`, `ollama`, `compatible`, `modal`) exactly
once.

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

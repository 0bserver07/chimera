---
title: "Proxy Provider"
description: "Proxy Provider"
---

`chimera.providers.proxy` implements a `Provider` that forwards every LLM
call to an HTTP relay instead of calling a model API directly.  This is useful
for centralised key management, cost tracking, or running agents in
air-gapped environments.

## ProxyProvider

| Constructor param | Type | Description |
|-------------------|------|-------------|
| `proxy_url` | `str` | Base URL of the relay (trailing slash stripped) |
| `auth_token` | `str \| None` | Optional Bearer token for proxy authentication |
| `model` | `str` | Model identifier forwarded in the request body |

The provider calls `POST {proxy_url}/api/complete` for non-streaming
completions.  The request body is standard Anthropic-compatible JSON
(`model`, `messages`, `tools`, `temperature`, `max_tokens`).

### Expected response schema

```json
{
  "content": "...",
  "tool_calls": [{"id": "...", "name": "...", "arguments": {}}],
  "usage": {"input_tokens": 0, "output_tokens": 0}
}
```

## Self-registration

The module calls `register_provider("proxy", _proxy_factory)` at import time.
The factory requires `base_url`; `api_key` is forwarded as the Bearer token.

## Usage

```python
from chimera.providers.factory import create_provider

provider = create_provider(
    provider_type="proxy",
    base_url="http://my-relay:8080",
    api_key="secret",
    model="glm-4-flash",
)

# Or via environment / config — same keyword args
```

The `context_window` property returns `128000` by default; configure your
relay to report the actual limit if needed.

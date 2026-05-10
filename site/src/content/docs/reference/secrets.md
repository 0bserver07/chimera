---
title: "chimera.secrets"
description: "Reference for chimera.secrets — secret registry, detector, redaction middleware."
---

`chimera.secrets` keeps API keys and credentials out of the agent's
context, logs, and event stream.

## Top-level exports

```python
from chimera.secrets import (
    SecretRegistry,
    SecretDetector,
    RedactionMiddleware,
)
```

| Symbol | Module | Purpose |
|---|---|---|
| `SecretRegistry` | `chimera.secrets.registry` | Register secret values (or env var names). `redact(text)` rewrites occurrences to `[REDACTED:NAME]`. |
| `SecretDetector` | `chimera.secrets.detector` | Pattern-matches against 10 built-in secret formats: API keys, AWS credentials, `Bearer ...` tokens, private keys, JWTs, ... |
| `RedactionMiddleware` | `chimera.secrets.redactor` | Plugs into `EventBus.add_middleware()` so every emitted event has secrets stripped before listeners see it. |

Pattern coverage in `SecretDetector` (the 10 built-ins):

- Anthropic API keys (`sk-ant-...`)
- OpenAI API keys (`sk-...`)
- AWS access key id + secret
- GitHub PATs (`ghp_...`, `gho_...`)
- Slack tokens (`xoxb-...`)
- `Bearer ...` Authorization headers
- PEM private keys
- JWTs
- Generic `password=...` / `token=...` URI components
- Stripe secret keys (`sk_live_...`)

## See also

- [`chimera.events`](/reference/events/) for `EventBus.add_middleware()`.
- [`chimera.security`](/reference/security/) for the risk-classification surface.

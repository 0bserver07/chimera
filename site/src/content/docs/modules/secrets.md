---
title: "Secrets"
description: "Secrets"
---

`chimera.secrets` prevents sensitive values -- API keys, tokens, passwords,
private keys -- from leaking into agent output, logs, or event streams.  It
provides three complementary layers: a **registry** for known secrets, a
**detector** for unknown secrets matched by regex patterns, and a
**redaction middleware** that scrubs secrets from the EventBus pipeline in
real time.

## Quick Start

```python
from chimera.secrets import SecretRegistry, SecretDetector, RedactionMiddleware

# 1. Register known secrets
registry = SecretRegistry()
registry.register("OPENAI_KEY", "sk-abc123456789012345678901234567890123")

# 2. Redact them from arbitrary text
text = "Using key sk-abc123456789012345678901234567890123 for auth"
print(registry.redact(text))
# -> "Using key [REDACTED] for auth"

# 3. Detect unknown secrets by pattern
detector = SecretDetector()
findings = detector.detect("export AWS_KEY=AKIAIOSFODNN7EXAMPLE")
# -> [{"pattern": "AKIA[0-9A-Z]{16}", "match": "AKIAIOSFODNN7EXAMPLE", ...}]
```

## Key Classes

| Class | Description |
|-------|-------------|
| `SecretRegistry` | Stores known secret name/value pairs and replaces them with `[REDACTED]` in text |
| `SecretDetector` | Pattern-based scanner with 10 built-in regexes for common secret formats |
| `RedactionMiddleware` | EventBus `Middleware` that redacts secrets from event fields before dispatch |

## Usage

### SecretRegistry

The registry tracks exact secret values by name and replaces longer secrets
first to avoid partial-match issues.

```python
from chimera.secrets import SecretRegistry

registry = SecretRegistry()

# Register individual secrets
registry.register("DB_PASSWORD", "hunter2")
registry.register("API_TOKEN", "tok_live_abcdef1234567890")

# Register from environment variables
registry.register_from_env("ANTHROPIC_API_KEY", "OPENAI_API_KEY")

# Register multiple at once
registry.register_from_dict({
    "SLACK_TOKEN": "xoxb-1234-5678",
    "GITHUB_TOKEN": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
})

# Redact all known secrets from text
output = registry.redact("Token is tok_live_abcdef1234567890, password is hunter2")
# -> "Token is [REDACTED], password is [REDACTED]"

# Check if text contains any secret
registry.contains_secret("Token is tok_live_abcdef1234567890")  # True

# List registered secret names (not values)
registry.secret_names  # ["DB_PASSWORD", "API_TOKEN", ...]
```

### SecretDetector

The detector scans text for patterns that look like secrets, even if they
are not registered.  It ships with 10 built-in patterns:

| Pattern category | Example match |
|-----------------|---------------|
| OpenAI API keys | `sk-abc123...` |
| Google API keys | `AIzaSy...` |
| GitHub PATs | `ghp_xxxx...` |
| GitLab PATs | `glpat-xxxx...` |
| AWS access keys | `AKIAIOSFODNN7EXAMPLE` |
| AWS secrets | `aws_secret_access_key = ...` |
| Generic password/token | `password = ...`, `api_key = ...` |
| Bearer tokens | `Bearer eyJhbG...` |
| Private keys | `-----BEGIN RSA PRIVATE KEY-----` |

```python
from chimera.secrets import SecretDetector

detector = SecretDetector()

# Detect secrets in text
findings = detector.detect("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload")
# -> [{"pattern": "Bearer\\s+...", "match": "Bearer eyJhbGciOiJIUzI1NiJ9.payload",
#       "start": 16, "end": 53}]

# Quick boolean check
detector.has_secrets("No secrets here")  # False
detector.has_secrets("key=sk-abcdef0123456789abcdef01")  # True

# Redact all detected secrets (even if not registered)
clean = detector.redact_detected("key=sk-abcdef0123456789abcdef01")
# -> "key=[REDACTED]"
```

You can extend the detector with custom patterns:

```python
detector = SecretDetector(extra_patterns=[
    r"my_corp_token_[a-z0-9]{32}",
    r"PRIVATE-KEY-\d{6}",
])
```

### RedactionMiddleware

`RedactionMiddleware` is an EventBus `Middleware` that scrubs secrets from
event fields (`output`, `text`, `content`, and string values in `metadata`)
before they reach any subscriber.

```python
from chimera.events import EventBus
from chimera.secrets import SecretRegistry, SecretDetector, RedactionMiddleware

bus = EventBus()
registry = SecretRegistry()
registry.register("API_KEY", "sk-secret-value-here-1234567890ab")

# Basic: redact only registered secrets
bus.use(RedactionMiddleware(registry))

# Advanced: also detect and redact unknown secrets by pattern
detector = SecretDetector()
bus.use(RedactionMiddleware(registry, detector=detector, detect_unknown=True))
```

When `detect_unknown=True`, the middleware first applies registry-based
redaction, then runs the detector's `redact_detected()` on the result.  This
catches secrets that were not explicitly registered but match known patterns.

## Integration

`RedactionMiddleware` plugs into the EventBus middleware chain.  Because
middleware runs before event handlers see the data, secrets are stripped from
tool results, streaming deltas, and any other events before they reach
logging, session storage, or external sinks.

```
Tool Result (with secret)
    │
    ▼
EventBus.publish(ToolResultEvent)
    │
    ▼
RedactionMiddleware.process()   ← secrets scrubbed here
    │
    ▼
Handlers receive clean event
```

The `SecretRegistry` and `SecretDetector` can also be used independently
outside the event system -- for example, to sanitize text before writing it
to a file or returning it to the user.

## Import Reference

```python
from chimera.secrets import SecretRegistry, SecretDetector, RedactionMiddleware
```

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/secrets/` -- verified against current source at wave-17.

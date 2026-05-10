---
title: "chimera.auth"
description: "Reference for chimera.auth — API-key, OAuth device, browser flows, and the credential store."
---

`chimera.auth` is a stdlib-only authentication layer: API keys, OAuth
device flow, OAuth browser flow, and a file-based credential store.

## Top-level exports

```python
from chimera.auth import (
    AuthManager,
    APIKeyAuth,
    OAuthDeviceFlow,
    OAuthBrowserFlow,
    CredentialStore,
)
```

| Symbol | Purpose |
|---|---|
| `AuthManager` | Coordinates flows, picks the right auth for a provider, persists tokens. |
| `APIKeyAuth` | Read API key from env var or credential store. |
| `OAuthDeviceFlow` | Polls `/device/code` + `/token` endpoints. Real stdlib HTTP impl, no extra deps. |
| `OAuthBrowserFlow` | Opens the user's browser, runs a localhost callback server, exchanges the code. |
| `CredentialStore` | File-based store at `~/.chimera/credentials.json` with `0o600` perms. |

`create_provider()` accepts an `auth_manager=` kwarg; if omitted, every
provider falls through to env-var lookup unchanged.

## See also

- [Use with Third-Party Providers](/use-with-third-party-providers/) for
  the env-var contracts each provider expects.
- [`chimera.providers`](/reference/providers/) for the
  `auth_manager`-aware factory.

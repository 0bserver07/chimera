---
title: "Auth"
description: "Auth"
---

`chimera.auth` manages credential lifecycle for LLM providers.  It supports
API key authentication out of the box and provides an OAuth scaffold for
device-flow and browser-based PKCE flows.

## Credential dataclass

Represents a single authentication credential:

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `str` | Provider identifier (e.g. `"anthropic"`, `"openai"`) |
| `token` | `str` | The bearer token or API key |
| `refresh_token` | `str \| None` | OAuth refresh token |
| `expires_at` | `float \| None` | Unix timestamp of expiry |
| `metadata` | `dict[str, Any]` | Arbitrary extra data |

The `is_expired` property returns `True` when `expires_at` is set and in the
past.  Credentials without an expiry are never considered expired.

## AuthProvider (ABC)

Every auth method implements:

| Method | Description |
|--------|-------------|
| `authenticate()` | Obtain a new `Credential` |
| `refresh(credential)` | Refresh an existing credential |
| `provider_name` (property) | Identifier of the target provider |

## Provider authentication matrix (Wave 11 B3)

`chimera auth login <provider>` does **not** assume every vendor
publishes a public OAuth device flow. Wave 11 (`B3-W11-OAUTH-CLEANUP`)
cleaned up the wave-9 scaffolds so each provider exits with
documented behaviour:

| Provider | Method | `chimera auth login` | Setup |
|---|---|---|---|
| `anthropic` | API key only | rc=2 + friendly stderr message | `export ANTHROPIC_API_KEY=sk-ant-...` |
| `openai` | API key only | rc=2 + friendly stderr message | `export OPENAI_API_KEY=sk-...` |
| `openrouter` | OAuth device flow (RFC 8628) | rc=0 + cred persisted | Stdlib polling against the public client. |
| `xai` | OAuth device flow (RFC 8628) | rc=0 + cred persisted | Same. |

The scaffold short-circuit lives in
`chimera.auth.oauth_device.SCAFFOLD_PROVIDERS` /
`scaffold_message()`. Anthropic and OpenAI endpoints stay empty
strings (sentinel) so accidentally promoting them to a real flow
requires touching the constants — not a config typo. See
[B3-W11 report](https://github.com/0bserver07/chimera/blob/master/research/B3-W11-OAUTH-CLEANUP.md).

## Built-in providers

### APIKeyAuth

Resolves an API key from (in priority order):
1. An explicit `key` parameter
2. A caller-specified environment variable (`env_var`)
3. A well-known environment variable for the provider

Well-known variables: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`.

```python
from chimera.auth import APIKeyAuth

auth = APIKeyAuth("anthropic")
credential = auth.authenticate()  # Reads ANTHROPIC_API_KEY

auth = APIKeyAuth("openai", key="sk-...")
credential = auth.authenticate()  # Uses the explicit key
```

### OAuthDeviceFlow

Implements RFC 8628 (device authorization grant) as a **real HTTP polling
implementation** using only stdlib (`urllib.request`).  Shows a code in the
terminal; the user visits a URL to authorize.  No external dependencies are
required.

The flow polls the token endpoint at `poll_interval` seconds until the user
completes authorization or `timeout` seconds elapse.  Standard RFC 8628 error
codes (`authorization_pending`, `slow_down`, `access_denied`, `expired_token`)
are handled automatically.

```python
from chimera.auth import OAuthDeviceFlow

auth = OAuthDeviceFlow(
    provider_name="custom",
    client_id="...",
    device_auth_url="https://provider.example/device/code",
    token_url="https://provider.example/oauth/token",
    poll_interval=5,
    timeout=300,
)
```

### OAuthBrowserFlow

OAuth 2.0 authorization code flow with PKCE and a local callback server.  This
is a **real PKCE implementation** using only stdlib (`urllib.request`,
`http.server`, `secrets`).  No external dependencies are required.

The flow generates a code verifier/challenge, opens the authorization URL in
the default browser, and spins up a temporary `http.server` listener on
`redirect_port` to capture the authorization code.  The code is then exchanged
for tokens at `token_url`.

```python
from chimera.auth import OAuthBrowserFlow

auth = OAuthBrowserFlow(
    provider_name="custom",
    client_id="...",
    auth_url="https://provider.example/authorize",
    token_url="https://provider.example/oauth/token",
    redirect_port=19876,
)
```

## CredentialStore

File-based credential persistence at `~/.chimera/credentials.json`.  The file
is written with `0o600` permissions (owner read/write only).

| Method | Description |
|--------|-------------|
| `get(provider)` | Return stored credential or `None` |
| `save(credential)` | Persist a credential to disk |
| `delete(provider)` | Remove a provider's credential |
| `list_providers()` | Return names of all stored providers |

## AuthManager facade

The `AuthManager` orchestrates the full credential lifecycle:

| Method | Description |
|--------|-------------|
| `register(auth_provider)` | Register a custom `AuthProvider` |
| `login(provider, method)` | Authenticate and cache; returns cached if valid |
| `get_token(provider)` | Return a valid token, refreshing if needed |
| `logout(provider)` | Remove stored credentials |

## Auth flow

```mermaid
sequenceDiagram
    participant App
    participant AuthManager
    participant CredentialStore
    participant AuthProvider

    App->>AuthManager: login("anthropic")
    AuthManager->>CredentialStore: get("anthropic")
    alt Cached & valid
        CredentialStore-->>AuthManager: Credential
    else Missing or expired
        AuthManager->>AuthProvider: authenticate()
        AuthProvider-->>AuthManager: Credential
        AuthManager->>CredentialStore: save(credential)
    end
    AuthManager-->>App: Credential
```

## Full example

```python
from chimera.auth import AuthManager

manager = AuthManager()
credential = manager.login("anthropic")
token = manager.get_token("anthropic")

# Later, clear credentials
manager.logout("anthropic")
```

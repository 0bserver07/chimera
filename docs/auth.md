---
title: Authentication
description: How Chimera authenticates with LLM providers — API keys, bearer tokens, and OAuth 2.0 device flow.
---

# Authentication

Chimera supports three credential sources, in this order of precedence at lookup time:

1. **Environment variables** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, …)
2. **`~/.chimera/auth.json`** — written by `chimera auth set …` (legacy API-key CLI)
3. **`~/.chimera/credentials.json`** — written by the OAuth flows (`chimera auth login`)

Both files are written with `0o600` permissions.

## API-key auth

Set an environment variable and you're done:

```bash
export ANTHROPIC_API_KEY=sk-ant-…
chimera code
```

## OAuth 2.0 device flow

For providers that publish a public OAuth client, you can authenticate without
ever pasting a key. The device flow is implemented in
[`chimera/auth/oauth_device.py`](https://github.com/0bserver07/chimera/blob/master/chimera/auth/oauth_device.py)
on top of `urllib.request` — no extra dependencies.

### CLI

```bash
chimera auth login openrouter        # public client, ready to go
chimera auth login xai               # public client, ready to go
chimera auth status                  # list stored credentials
chimera auth logout openrouter       # remove a credential
```

### How it works

`chimera auth login <provider>` runs RFC 8628:

1. POSTs to the device-authorization endpoint to fetch a `device_code` and a
   short `user_code`.
2. Prints `Visit <verification_url>, enter code: <user_code>` and copies the
   code to the system clipboard if `pbcopy` / `xclip` / `xsel` / `clip` is
   available.
3. Polls the token endpoint with the standard back-off rules:
   - `authorization_pending` → keep polling
   - `slow_down` → bump the interval by 5 seconds
   - `access_denied`, `expired_token` → fail loudly
4. On success, persists the resulting `Credential` (with `refresh_token` and
   `expires_at`) to `~/.chimera/credentials.json` via `CredentialStore`.

### Provider presets

| Provider     | Status                          | Notes |
|--------------|---------------------------------|-------|
| `openrouter` | Public client baked in          | Scope: `completion` |
| `xai`        | Public client baked in          | Scope: `api` |
| `anthropic`  | Placeholder — overrides required | No public device-flow client published |
| `openai`     | Placeholder — overrides required | No public device-flow client published |

### Custom client / endpoints

Anthropic and OpenAI do not currently publish a device-flow client. You can
still drive the same flow if you have your own client registration:

```bash
chimera auth login anthropic \
  --client-id my-app-cid \
  --device-url https://example/oauth/device/code \
  --token-url https://example/oauth/device/token \
  --scope read --scope write
```

Or pass `--no-clipboard` to skip the clipboard copy.

### Library use

```python
from chimera.auth.oauth_device import OAuthDeviceFlow, login

# High-level helper
cred = login("openrouter")

# Or build the flow yourself
flow = OAuthDeviceFlow(
    provider="my-host",
    client_id="cid",
    scopes=["api"],
    device_url="https://my-host/oauth/device/code",
    token_url="https://my-host/oauth/device/token",
)
cred = flow.authenticate()        # device prompt + poll + persist
fresh = flow.refresh(cred)         # uses cred.refresh_token
```

### Testing

The flow is fully unit-tested in [`tests/auth/test_oauth_device.py`](https://github.com/0bserver07/chimera/blob/master/tests/auth/test_oauth_device.py)
with a scripted `urlopen` opener — no real network calls. The opener supports:

- 200 + JSON response
- 400/500 `urllib.error.HTTPError` with arbitrary body
- arbitrary callables for fine-grained inspection

If you're integrating a new provider, mirror that pattern: feed your
`OAuthDeviceFlow` an `opener=` and a `sleep=` callback so tests stay
hermetic.

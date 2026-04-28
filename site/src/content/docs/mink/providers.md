---
title: "Mink Providers"
description: "Supported provider chain for chimera mink: Ollama (local + cloud), Anthropic, OpenAI, Google, and compatible endpoints."
---

# Providers — what mink can talk to

`chimera mink` defaults to Ollama (`kimi-k2.6:cloud`, see
`chimera/mink/cli.py:34`), but the underlying provider stack is pluggable
through `chimera.providers.create_provider()`
(`chimera/providers/factory.py:36`). Six adapters self-register at import
time via `_ensure_builtins_registered()`
(`chimera/providers/registry.py:55`); a seventh (`proxy`) is registered by
`chimera/providers/proxy.py:119`.

## Built-in registry

| Name          | Module                            | Self-registers at        |
|---------------|-----------------------------------|--------------------------|
| `anthropic`   | `chimera/providers/anthropic.py`  | line 437                 |
| `openai`      | `chimera/providers/openai.py`     | line 375                 |
| `google`      | `chimera/providers/google.py`     | line 165                 |
| `ollama`      | `chimera/providers/ollama.py`     | line 392                 |
| `compatible`  | `chimera/providers/compatible.py` | line 150                 |
| `modal`       | `chimera/providers/modal.py`      | line 171                 |
| `proxy`       | `chimera/providers/proxy.py`      | line 119                 |

`create_provider()` picks one of these by:
1. explicit `provider_type=`,
2. prefix on the model name (`claude-*`, `gpt-*`, `gemini-*`, `glm-*`,
   `kimi-*`, `llama*`, `qwen*`, `mistral*`, `phi*`),
3. `ProviderCatalog` lookup (`chimera/providers/catalog.py:79`),
4. loose env-var fallback.

See `_infer_provider()` (`chimera/providers/factory.py:113`) for the exact
order, including the env-var override that routes anything with
`ANTHROPIC_BASE_URL` or `ANTHROPIC_AUTH_TOKEN` set through the Anthropic
adapter.

---

## Ollama (default for mink)

Local or Ollama-Cloud models served by the `ollama` daemon. The adapter
talks to `/api/chat` (NOT the OpenAI-compat shim at `/v1/...`, which
silently drops `tool_calls` from streaming chunks; see comment at
`chimera/providers/ollama.py:25-32`).

- **Use when:** running locally for privacy, or hitting Ollama Cloud
  (`*:cloud` tags) for hosted Kimi / Qwen3 / GLM-4.6.
- **Setup:**
  ```bash
  brew install ollama        # or: curl -fsSL https://ollama.com/install.sh | sh
  ollama serve &             # daemon on :11434
  ollama signin              # only needed for :cloud tags
  ollama pull qwen3:32b      # local fallback
  ```
- **Auth options:** none for local; Ollama Cloud uses `ollama signin`
  (browser flow stored in the daemon, not in `~/.chimera/`).
- **Use with mink:**
  ```bash
  chimera mink --model kimi-k2.6:cloud -p "list files then read README.md"
  chimera mink --model qwen3:32b       -p "explain this repo"
  ```
- **What's wired:** streaming yes (NDJSON over `/api/chat`,
  `chimera/providers/ollama.py:214`); tool calls yes (native
  `tool_calls` field, `chimera/providers/ollama.py:269-295`); thinking
  yes for `kimi*` only (`chimera/providers/ollama.py:50`); JSON-mode no
  on cloud Kimi (server ignores `format` field, see `quickstart.md`);
  vision varies by model (Kimi 2.6 weak); prompt caching no.
- **Defaults:** `num_ctx=131072`, `keep_alive="60m"`, `OLLAMA_HOST` env
  override, `262144` ctx auto-bumped for `kimi*` (see
  `chimera/mink/cli.py:300`).
- **Limits:** `tool_choice="required"` is silently dropped when
  `think:true` (`chimera/providers/ollama.py:103`); `:cloud` weights live
  on Moonshot/Ollama infra; first cold-start call can take 10-30s.

---

## Anthropic

Anthropic SDK against the official `api.anthropic.com` endpoint, OR
against any Anthropic-wire-compatible endpoint via `ANTHROPIC_BASE_URL`
(GLM-4.6 via `api.z.ai`, Moonshot, Ollama's Anthropic-compat shim, etc.).

- **Use when:** you have an Anthropic API key, OR you have a third-party
  provider that speaks the Anthropic Messages API.
- **Setup:**
  ```bash
  uv sync --extra anthropic               # installs the anthropic SDK
  export ANTHROPIC_API_KEY=sk-ant-...
  # OR, for an Anthropic-compat endpoint:
  export ANTHROPIC_BASE_URL=https://api.z.ai/v1/anthropic
  export ANTHROPIC_AUTH_TOKEN=...
  ```
- **Auth options:**
  - `ANTHROPIC_API_KEY` — first-class env var
    (`chimera/providers/anthropic.py:53`)
  - `ANTHROPIC_AUTH_TOKEN` — Bearer-token alias for OAuth-issued tokens
    or third-party endpoints (same line, fallback)
  - `AuthManager.get_token("anthropic")` — pulls from
    `~/.chimera/auth.json` or environment
    (`chimera/auth/manager.py:36-37`, `chimera/providers/anthropic.py:48`)
  - OAuth device or browser flow via `OAuthDeviceFlow`/`OAuthBrowserFlow`
    (`chimera/auth/oauth.py:20`, `chimera/auth/oauth.py:182`); tokens
    persist to `~/.chimera/credentials.json` (mode `0o600`,
    `chimera/auth/store.py:62`)
- **Use with mink:**
  ```bash
  chimera mink --model claude-sonnet-4-20250514 -p "review this PR"
  ```
- **What's wired:** streaming yes (`chimera/providers/anthropic.py:204`);
  tool calls yes; native async (`AsyncAnthropic`,
  `chimera/providers/anthropic.py:345`); extended thinking yes
  (`enable_thinking=True`, `chimera/providers/anthropic.py:120-125`);
  prompt caching yes (opt-in via `enable_cache=True`,
  `chimera/providers/anthropic.py:131-143`); vision yes (Claude models);
  JSON mode via tool-calling pattern.
- **Limits:** when `enable_thinking` is on, temperature is forced to 1
  (`chimera/providers/anthropic.py:125`); `ImportError` if
  `chimera-run[anthropic]` is not installed.

---

## OpenAI

The official `openai` SDK against `api.openai.com`, or any
`/v1/chat/completions` endpoint via `base_url=`.

- **Use when:** GPT-4o, o1, o3, Codex, or any provider that ships an
  OpenAI-SDK-compatible endpoint and you want native streaming +
  reasoning-token tracking.
- **Setup:**
  ```bash
  uv sync --extra openai                  # installs the openai SDK
  export OPENAI_API_KEY=sk-...
  ```
- **Auth options:**
  - `OPENAI_API_KEY` (`chimera/providers/openai.py:52`)
  - `AuthManager.get_token("openai")`
    (`chimera/providers/openai.py:46-50`)
- **Use with mink:**
  ```bash
  chimera mink --model gpt-4o          -p "draft a release note"
  chimera mink --model o3-mini         -p "prove this invariant"
  ```
- **What's wired:** streaming yes (`chimera/providers/openai.py:142`);
  tool calls yes (delta-accumulated by `index`,
  `chimera/providers/openai.py:174-213`); native async (`AsyncOpenAI`,
  `chimera/providers/openai.py:222`); reasoning-token tracking for o-series
  models (`chimera/providers/openai.py:96-99`); prompt-cache hit accounting
  (`chimera/providers/openai.py:100-104`); vision yes (gpt-4o); JSON mode
  yes (via standard SDK options the adapter passes through unchanged).
- **Limits:** thinking-level enum is accepted but not forwarded — the
  provider relies on the OpenAI SDK's native reasoning behavior; no
  Anthropic-style cache control.

---

## OpenAI-compatible

Generic adapter for any `/v1/chat/completions` endpoint. Used by
OpenRouter, Together, Fireworks, Groq, vLLM, SGLang, LM Studio, LiteLLM,
Anthropic Coding API in OpenAI-compat mode, and `bedrock/`/`azure/`/etc.
catalog entries (`chimera/providers/catalog.py:57-76`).

- **Use when:** you have an OpenAI-shaped endpoint and don't want the
  full `openai` SDK dependency.
- **Setup:**
  ```bash
  # No extra needed — uses httpx (already a dep of mink/Ollama).
  export OPENAI_API_KEY=...               # or any bearer-style token
  ```
- **Auth options:** `OPENAI_API_KEY` env (or any token passed as
  `api_key=`); custom headers via the `headers=` constructor kwarg
  (`chimera/providers/compatible.py:29`).
- **Use with mink:**
  ```bash
  # via the catalog (resolves base_url + api_key from env)
  chimera mink --model groq/llama-3.3-70b -p "..."
  chimera mink --model deepseek-chat      -p "..."
  ```
- **What's wired:** non-streaming `complete()` only
  (`chimera/providers/compatible.py:44`); tool calls yes; default
  ASYNC/streaming falls back to base-class wrappers (`base.py:61`,
  `base.py:107`); no thinking, no caching.
- **Limits:** **no native streaming** — the base-class `stream()` shim
  yields the whole response as one chunk; this is fine for short
  completions but loses token-by-token feel. Use the `openai` adapter if
  you need real streaming against an OpenAI-shaped endpoint.

---

## Google Gemini

Google `google-generativeai` SDK against the Gemini API.

- **Use when:** you want Gemini 2.0 Flash, Gemini 1.5 Pro, etc.
- **Setup:**
  ```bash
  uv sync --extra google                  # if shipped; otherwise:
  pip install google-generativeai
  export GOOGLE_API_KEY=...               # or GEMINI_API_KEY
  ```
- **Auth options:**
  - `GOOGLE_API_KEY` or `GEMINI_API_KEY`
    (`chimera/auth/manager.py:39`, `chimera/providers/google.py:45`)
  - `AuthManager.get_token("google")`
- **Use with mink:**
  ```bash
  chimera mink --model gemini-2.0-flash -p "summarize this doc"
  ```
- **What's wired:** non-streaming `complete()` only; tool calls yes
  (function declarations, `chimera/providers/google.py:120`); 1M context
  windows (`chimera/providers/google.py:23-26`); system messages folded
  into the first user turn as `[System] ...`
  (`chimera/providers/google.py:101`).
- **Limits:** **no streaming** — the base-class shim is used; no async
  override; system-prompt handling is approximate (Gemini wants a
  separate `system_instruction` field, this adapter inlines it instead);
  schema cleanup strips `$ref` / `oneOf` / `additionalProperties` etc.
  unsupported by Gemini (`chimera/providers/google.py:132-146`); thinking
  param is accepted but ignored.

---

## Modal

Modal-hosted vLLM container exposing OpenAI-shape `/v1/chat/completions`.
The adapter does not deploy the container — it just calls a URL you
already have.

- **Use when:** you've deployed an open-weight model on Modal GPUs and
  want mink to drive it.
- **Setup:**
  ```bash
  pip install modal httpx
  modal token new                         # browser flow
  export MODAL_TOKEN_ID=...               # optional, if you want to skip browser
  export MODAL_TOKEN_SECRET=...
  # then deploy your container and capture its public URL
  ```
- **Auth options:** `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` env vars
  (`chimera/providers/modal.py:47-48`); per-container auth is up to your
  vLLM image.
- **Use with mink:** not directly — `mink --model` only auto-routes to
  Ollama. Use the catalog or `create_provider()` from Python:
  ```python
  from chimera.providers import create_provider
  provider = create_provider(
      "modal", model="meta-llama/Llama-3.3-70B",
      base_url="https://your-org--llm-app-serve.modal.run/v1",
  )
  ```
- **What's wired:** non-streaming `complete()` only; tool calls yes (via
  the OpenAI-compat shape vLLM emits).
- **Limits:** **no streaming, no async, no thinking, no caching**;
  `base_url` is required — `_get_base_url()` raises if you forget it
  (`chimera/providers/modal.py:55`).

---

## Proxy

HTTP relay for centralized key management, cost tracking, or running
agents in environments without direct API egress.

- **Use when:** a team wants one server holding the API keys, with mink
  clients pointing at it. The proxy translates Chimera's wire format to
  whatever upstream provider it controls.
- **Setup:** stand up a server that exposes `POST /api/complete` (and
  optionally `/api/stream`) accepting Chimera's payload shape; see
  `chimera/providers/proxy.py:18-26` for the contract.
- **Auth options:** Bearer token via the `api_key=` constructor arg, sent
  as `Authorization: Bearer <token>` (`chimera/providers/proxy.py:69`).
- **Use with mink:** like Modal, only via `create_provider()` from
  Python:
  ```python
  from chimera.providers import create_provider
  provider = create_provider(
      "proxy",
      base_url="http://proxy.internal:8000",
      api_key="team-token",
      model="claude-sonnet-4",
  )
  ```
- **What's wired:** synchronous `complete()` only (uses stdlib
  `urllib.request`, no `httpx`); tool calls forwarded as plain dicts;
  thinking level forwarded as a string for the proxy to interpret
  (`chimera/providers/proxy.py:62-65`).
- **Limits:** no streaming, no async; the proxy must report `usage`
  itself — the adapter trusts whatever JSON comes back; default
  `context_window=128000` is hardcoded (`chimera/providers/proxy.py:97`).

---

## Custom providers

Implement `Provider` (`chimera/providers/base.py:47`) and call
`register_provider("my-name", factory)` (`chimera/providers/registry.py:14`).
Factory signature: `factory(model=..., api_key=..., base_url=..., **kw)`.
After registration, `create_provider("my-name", model=...)` works
identically to the built-ins. Drop the factory call into your package's
`__init__.py` so import triggers self-registration, mirroring the
built-ins (e.g. `chimera/providers/anthropic.py:436`).

---

## Choosing a provider

| Concern         | Pick                                                              |
|-----------------|-------------------------------------------------------------------|
| Privacy / local | `ollama` with a local model (`qwen3:32b`, `llama3.1:70b-instruct`) |
| Lowest latency  | `ollama` local on GPU; `groq/*` via `compatible`                  |
| Best capability | `anthropic` (claude-sonnet/opus) or `openai` (gpt-4o, o3)         |
| Cost-sensitive  | `compatible` with `deepseek-chat` or `groq/llama-3.3-70b`         |
| Vision-heavy    | `anthropic` (claude-sonnet) or `openai` (gpt-4o); avoid Kimi 2.6  |
| Long context    | `google` (Gemini 1M) or `anthropic` (Claude 200k) or Kimi (262k)  |
| Tools + streaming + thinking, all in one | `anthropic` (claude with `enable_thinking=True`) |

---

## Mixing providers in one session

Two ways to set the model — last write wins:

1. CLI flag (highest precedence): `chimera mink --model <name>`.
2. `settings.json` `model:` key, loaded by
   `load_mink_settings()` (`chimera/mink/settings.py:274`). The CLI
   default `kimi-k2.6:cloud` is treated as "user did not pass --model"
   so an agent's frontmatter `model:` can override it
   (`chimera/mink/cli.py:796-801`).
3. Env vars `ANTHROPIC_MODEL` / `OPENAI_MODEL` are picked up only when no
   `--model` and no settings model is set
   (`chimera/providers/factory.py:81-82`).

A single `chimera mink` process holds one provider for the whole
session. To swap mid-session, exit and relaunch (or, programmatically,
build a new `Agent` with `create_provider(...)`).

---

## Credentials directory

| Path                              | Source                                | Mode    |
|-----------------------------------|---------------------------------------|---------|
| `~/.chimera/credentials.json`     | OAuth-issued tokens, refresh tokens   | `0o600` |
| `~/.chimera/auth.json`            | `AuthManager.set_token()`             | default |
| `~/.chimera/sessions/*.jsonl`     | Session tree per-cwd (mink REPL)      | default |
| `~/.chimera/eventlog/mink-*/`     | `chimera mink -p` persisted runs      | default |

`CredentialStore._write` chmods to `0o600` after each save
(`chimera/auth/store.py:62`); `auth.json` is a plain JSON file written
by `AuthManager.set_token()` (`chimera/auth/manager.py:154`).

---

## Proxy mode for teams

For an org-wide gateway sitting in front of Anthropic-shaped providers:

```bash
export ANTHROPIC_BASE_URL=http://proxy.internal:8000
export ANTHROPIC_AUTH_TOKEN=team-issued-jwt
chimera mink --model claude-sonnet-4 -p "..."
```

`AnthropicProvider` honors both env vars
(`chimera/providers/anthropic.py:53,58`); the proxy can be a thin pass-
through, a key-rotating relay, or a budget-enforcing gatekeeper. The
dedicated `proxy` provider (above) is the alternative when your gateway
speaks its own JSON shape rather than the Anthropic wire protocol.

---

## Troubleshooting

| Symptom                                       | Likely cause / fix                                                                                                |
|-----------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| `401` / `403`                                 | Wrong key, wrong env var. Check `AuthManager.status()` or `printenv | grep -E '(ANTHROPIC|OPENAI|GOOGLE)_'`.       |
| `ImportError: pip install chimera-run[...]`   | Provider's optional extra is missing. `uv sync --extra anthropic` (or `openai`, `google`).                        |
| `Cannot infer provider from model name '...'` | Pass `provider_type=...` explicitly, or use a known prefix; see `_infer_provider()` for the list.                 |
| Streaming hangs / first call is slow          | Cloud cold start. Ollama Cloud needs `keep_alive: 60m` (mink sets this). Anthropic / OpenAI typically warm in <2s.|
| `tool_calls` always empty on Ollama           | You hit `/v1/chat/completions` instead of `/api/chat`. Set `OLLAMA_HOST` to the daemon root, not `.../v1`.        |
| `temperature must be 1 with thinking`         | Anthropic extended thinking forces `temperature=1` (see `anthropic.py:125`). Drop your custom temperature.        |
| Ollama Kimi rejects `tool_choice: "required"` | Adapter silently drops it when `think:true` is also set (`ollama.py:103`); use `auto`/`none`.                     |
| Gemini complains about `$ref` / `oneOf`       | The adapter strips them (`google.py:132`); if you see it pass through, check you're hitting the right adapter.    |

---
title: Otter Providers
description: Provider chain for chimera otter — Anthropic, OpenAI, OpenRouter, Ollama, Modal, and custom registrations.
---

# Providers — what otter can talk to

`chimera otter` reuses Chimera's standard provider stack
(`chimera/providers/factory.py`), so any provider that mink can drive,
otter can drive. The difference is in defaults: otter's resolver
(`chimera/otter/providers.py`) prefers a hosted provider chain — Anthropic
first, then OpenRouter, then OpenAI — because the upstream open-source
agent it parallels is server-first and almost always run against a hosted
LLM.

This page is the otter-specific layer on top of the provider matrix.
For the deep, line-numbered tour of every adapter (Ollama internals,
Anthropic streaming, OpenAI delta accumulation, etc.) see
[`docs/mink/providers.md`](../mink/providers.md). The same code is in play.

## Resolution order

`build_provider(args)` (`chimera/otter/providers.py:116`) walks this chain
on every otter invocation, first match wins:

1. Explicit `args.model` (CLI `--model <id>`).
2. `$OTTER_MODEL` environment variable.
3. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
4. `$OPENROUTER_API_KEY` set → defaults to `anthropic/claude-sonnet-4`,
   routed through the OpenAI-compatible adapter against `openrouter.ai`.
5. `$OPENAI_API_KEY` set → defaults to `gpt-4o`.
6. Friendly error pointing at the three env vars above.

Explicit beats env beats default. So `chimera otter --model gpt-4o -p "..."`
works even when `$ANTHROPIC_API_KEY` is set in the environment.

## Anthropic (default)

The first-class otter target. `claude-sonnet-4-6` is the default
because it's the model the rest of the Chimera repo benchmarks against,
streams and tool-calls cleanly, and supports extended thinking + prompt
caching for long sessions.

- **Setup:**
  ```bash
  uv sync --extra anthropic
  export ANTHROPIC_API_KEY=sk-ant-...
  ```
- **Use with otter:**
  ```bash
  chimera otter -p "review this PR"
  chimera otter --model claude-opus-4 -p "long-form refactor"
  ```
- **What's wired:** streaming, tool calls, async, extended thinking,
  prompt caching, vision. See `chimera/providers/anthropic.py`.
- **Anthropic-compatible endpoints:** the same provider also accepts
  `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`, so you can route otter
  through GLM-4.6 (`api.z.ai`), Moonshot, or any third-party gateway
  that speaks the Messages API:
  ```bash
  export ANTHROPIC_BASE_URL=https://api.z.ai/v1/anthropic
  export ANTHROPIC_AUTH_TOKEN=...
  chimera otter --model glm-4.6 -p "..."
  ```

## OpenAI

Use when you have an OpenAI key and want `gpt-4o`, `o3`, or any GPT-5
class model otter recognizes.

- **Setup:**
  ```bash
  uv sync --extra openai
  export OPENAI_API_KEY=sk-...
  ```
- **Use with otter:**
  ```bash
  chimera otter --model gpt-4o -p "draft a release note"
  chimera otter --model o3-mini -p "prove this invariant"
  ```
- **What's wired:** native streaming, tool calls, async (`AsyncOpenAI`),
  reasoning-token tracking for o-series, prompt-cache hit accounting,
  vision (`gpt-4o`), JSON mode. See `chimera/providers/openai.py`.

## OpenRouter

OpenRouter is one of otter's first-class targets because its `vendor/name`
model id convention (e.g. `anthropic/claude-sonnet-4`,
`google/gemini-2.5-pro`, `meta-llama/llama-3.3-70b`) lets otter swap
upstream brands behind a single API key.

Routing rule: when `$OPENROUTER_API_KEY` is set **and** the resolved
model id contains a `/`, otter hands it to the OpenAI-compatible adapter
pointed at `https://openrouter.ai/api/v1`
(`chimera/otter/providers.py:96-113`). A bare `claude-sonnet-4-6` with
both `$OPENROUTER_API_KEY` and `$ANTHROPIC_API_KEY` set still goes
direct to Anthropic; the `/` separator is the explicit signal.

- **Setup:**
  ```bash
  export OPENROUTER_API_KEY=sk-or-...
  ```
- **Use with otter:**
  ```bash
  chimera otter --model anthropic/claude-sonnet-4 -p "..."
  chimera otter --model google/gemini-2.5-pro -p "..."
  chimera otter --model meta-llama/llama-3.3-70b -p "..."
  ```
- **What's wired:** non-streaming `complete()` plus base-class shim for
  streaming (one chunk at a time). Tool calls forwarded as standard
  OpenAI deltas. No provider-side caching.

## Ollama (local + cloud tags)

Identical to mink: same `OllamaProvider`, same `/api/chat` endpoint,
same `keep_alive: 60m`, same `:cloud` tag handling.

- **Setup:**
  ```bash
  brew install ollama        # or curl -fsSL https://ollama.com/install.sh | sh
  ollama serve &             # daemon on :11434
  ollama signin              # only needed for :cloud tags
  ollama pull qwen3:32b      # local fallback
  ```
- **Use with otter:**
  ```bash
  chimera otter --model qwen3:32b -p "explain this repo"
  chimera otter --model glm-5.1:cloud -p "summarize"
  chimera otter --model kimi-k2.6:cloud -p "long-context refactor"
  ```
- **What's wired:** native streaming over NDJSON, tool calls (`tool_calls`
  array on `done:false` chunks), `think:true` for `kimi*` tags,
  per-request `num_ctx`, configurable `OLLAMA_HOST` for remote daemons.

## Modal

Modal-hosted vLLM container exposing OpenAI-shape `/v1/chat/completions`.
Otter inherits the same adapter as mink — useful when you've stood up an
open-weight model on Modal and want the otter loop to drive it.

- **Setup:**
  ```bash
  pip install modal httpx
  modal token new
  ```
- **Use with otter:** `--model` only auto-routes Anthropic / OpenAI /
  OpenRouter / Ollama. To call Modal, build the provider in Python:
  ```python
  from chimera.providers import create_provider
  provider = create_provider(
      "modal", model="meta-llama/Llama-3.3-70B",
      base_url="https://your-org--llm-app-serve.modal.run/v1",
  )
  ```
- **What's wired:** non-streaming `complete()`, tool calls. No streaming,
  no async, no thinking, no caching.

## Custom providers

Implement `Provider` (`chimera/providers/base.py`) and call
`register_provider("my-name", factory)`
(`chimera/providers/registry.py`). Factory signature:
`factory(model=..., api_key=..., base_url=..., **kw)`. After
registration, `create_provider("my-name", model=...)` works identically
to the built-ins.

To make otter pick your provider automatically, either:

1. Hand it an explicit model with a recognizable prefix
   (e.g. add `myco-*` to `_infer_provider`), or
2. Construct the provider yourself and pass it to the otter agent
   builder (the lower-level entry point used by `chimera otter serve`):
   ```python
   from chimera.otter.providers import build_provider
   # … build args with --model and the env keys you need …
   provider = build_provider(args)
   ```

The custom factory call can live in your package's `__init__.py` so
import triggers self-registration, mirroring the built-ins (e.g.
`chimera/providers/anthropic.py:436`).

## Choosing a provider

| Concern | Pick |
|---|---|
| Default, "just works" | `anthropic` (claude-sonnet-4-6) |
| One key, many vendors | `openrouter` (`anthropic/...`, `google/...`, `meta-llama/...`) |
| Privacy / local | `ollama` with `qwen3:32b` or any tool-capable local tag |
| Cheap + fast | `compatible` against Groq / DeepSeek / Together |
| Vision-heavy | `anthropic` (claude) or `openai` (gpt-4o) |
| Long context (>200k) | `google` (Gemini 1M), `anthropic` (200k), Kimi (262k) |
| Reasoning-tuned | `openai` (o3, o3-mini) or `anthropic` (claude-opus + thinking) |

## Mixing providers in one session

Otter holds **one provider per process**. To swap mid-session, exit and
re-launch with a different `--model` or different env. The HTTP server
(`chimera otter serve`) likewise binds one provider per process; to fan
out across providers, run multiple servers on different ports.

The REPL `/model` slash command cycles through the model list passed
via `--models <a>,<b>,<c>` — it rebuilds the provider on each switch.

## Auth storage

Otter shares Chimera's credential storage with mink and the rest of the
CLI:

| Path | Source | Mode |
|---|---|---|
| `~/.chimera/credentials.json` | OAuth-issued tokens, refresh tokens | `0o600` |
| `~/.chimera/auth.json` | `AuthManager.set_token()` | default |
| `~/.opencode/config.json` | Optional read-only config ingest | (untouched) |

`CredentialStore._write` chmods to `0o600` after each save
(`chimera/auth/store.py:62`).

## Proxy mode for teams

For an org-wide gateway sitting in front of Anthropic-shaped providers:

```bash
export ANTHROPIC_BASE_URL=http://proxy.internal:8000
export ANTHROPIC_AUTH_TOKEN=team-issued-jwt
chimera otter --model claude-sonnet-4-6 -p "..."
```

`AnthropicProvider` honors both env vars; the proxy can be a thin
pass-through, a key-rotating relay, or a budget-enforcing gatekeeper.
The dedicated `proxy` provider (registered by
`chimera/providers/proxy.py`) is the alternative when your gateway
speaks its own JSON shape rather than the Anthropic wire protocol.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `otter: no provider configured` | Set one of `$ANTHROPIC_API_KEY`, `$OPENROUTER_API_KEY`, `$OPENAI_API_KEY`, or pass `--model <id>` / `$OTTER_MODEL`. |
| `401` / `403` | Wrong key or wrong env var. `printenv \| grep -E '(ANTHROPIC\|OPENAI\|OPENROUTER)_'` to verify. |
| OpenRouter not used despite key | Model id needs the `vendor/name` `/` separator. Bare `claude-sonnet-4-6` routes to Anthropic. |
| `ImportError: pip install chimera-run[anthropic]` | `uv sync --extra anthropic` (or `openai`, `google`) to pull the SDK. |
| `Cannot infer provider from model name '...'` | Pass `--model <id>` with a known prefix (`claude-*`, `gpt-*`, `gemini-*`, `glm-*`, `kimi-*`, `qwen*`, …) or hardcode `provider_type=` via `build_provider`. |
| Streaming hangs on first call | Cloud cold start. Anthropic / OpenAI typically warm in <2s; Ollama Cloud needs `keep_alive: 60m` (otter sets this). |
| `tool_calls` always empty on Ollama | You hit `/v1/chat/completions` instead of `/api/chat`. Set `OLLAMA_HOST` to the daemon root, not `.../v1`. |
| `temperature must be 1 with thinking` | Anthropic extended thinking forces `temperature=1`. Drop your custom temperature kwarg. |

## See also

- [`models.md`](models.md) — concrete model id catalogue + `OTTER_MODEL`.
- [`docs/mink/providers.md`](../mink/providers.md) — line-numbered deep
  dive into every provider adapter; the same code drives otter.
- [`server.md`](server.md) — provider holds across the HTTP server's
  lifetime; one server, one provider.

---
title: Weasel Providers
description: Provider chain for chimera weasel — Anthropic, OpenAI, OpenRouter, Ollama, llama.cpp, and custom registrations. Resolution order, env vars, per-provider notes, and troubleshooting.
---

# Providers — what weasel can talk to

`chimera weasel` reuses Chimera's standard provider stack
(`chimera/providers/factory.py`), so any provider that mink, otter,
or ferret can drive, weasel can drive. The difference is in defaults
and the resolution chain: weasel's resolver
(`chimera/weasel/providers.py`) prefers the broadest reach — it will
fall back to a local Ollama daemon if no hosted key is set, so the
zero-config path works on a fresh laptop.

This page is the weasel-specific layer. For the deep, line-numbered
tour of every adapter, see
[`docs/mink/providers.md`](../mink/providers.md). Same code, deeper
notes.

## Resolution order

`build_provider(args)` walks this chain on every weasel invocation,
first match wins:

1. Explicit `args.model` (CLI `--model <id>`, SDK `model=`).
2. `$WEASEL_MODEL` environment variable.
3. `$ANTHROPIC_API_KEY` set → defaults to `claude-sonnet-4-6`.
4. `$OPENAI_API_KEY` set → defaults to `gpt-4o`.
5. `$OPENROUTER_API_KEY` set → defaults to `anthropic/claude-sonnet-4`.
6. Local Ollama daemon reachable on `$OLLAMA_HOST` (default
   `http://localhost:11434`) → first installed tag.
7. Friendly error pointing at the env vars above.

Explicit beats env beats default. So
`chimera weasel --model gpt-4o -p "..."` works even when
`$ANTHROPIC_API_KEY` is set.

## Anthropic (default for hosted)

The first-class hosted target. `claude-sonnet-4-6` is the default
when `$ANTHROPIC_API_KEY` is set: it streams, tool-calls cleanly,
supports extended thinking, and has prompt caching for long sessions.

- **Setup:**
  ```bash
  uv sync --extra anthropic
  export ANTHROPIC_API_KEY=sk-ant-...
  ```
- **Use:**
  ```bash
  chimera weasel -p "review this PR"
  chimera weasel --model claude-opus-4 -p "long-form refactor"
  ```
- **Wired:** streaming, tool calls, async, extended thinking,
  prompt caching, vision. See `chimera/providers/anthropic.py`.
- **Anthropic-compatible endpoints:** the same provider also accepts
  `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN`, so weasel can route
  through GLM-4.6, Moonshot, or any third-party gateway that speaks
  the Messages API:
  ```bash
  export ANTHROPIC_BASE_URL=https://api.z.ai/v1/anthropic
  export ANTHROPIC_AUTH_TOKEN=...
  chimera weasel --model glm-4.6 -p "..."
  ```

## OpenAI

Use when you have an OpenAI key and want `gpt-4o`, `o3`, or any
GPT-5-class model weasel recognizes.

- **Setup:**
  ```bash
  uv sync --extra openai
  export OPENAI_API_KEY=sk-...
  ```
- **Use:**
  ```bash
  chimera weasel --model gpt-4o -p "draft a release note"
  chimera weasel --model o3-mini -p "prove this invariant"
  ```
- **Wired:** native streaming, tool calls, async (`AsyncOpenAI`),
  reasoning-token tracking for o-series, prompt-cache hit accounting,
  vision (`gpt-4o`), JSON mode.

## OpenRouter

OpenRouter is one of weasel's first-class targets because the
`vendor/name` model id convention
(`anthropic/claude-sonnet-4`, `google/gemini-2.5-pro`,
`meta-llama/llama-3.3-70b`) lets a single key fan out across
providers.

Routing rule: when `$OPENROUTER_API_KEY` is set **and** the resolved
model id contains a `/`, weasel hands it to the OpenAI-compatible
adapter pointed at `https://openrouter.ai/api/v1`. A bare
`claude-sonnet-4-6` with both `$OPENROUTER_API_KEY` and
`$ANTHROPIC_API_KEY` set still goes direct to Anthropic — the `/`
separator is the explicit signal.

- **Setup:**
  ```bash
  export OPENROUTER_API_KEY=sk-or-...
  ```
- **Use:**
  ```bash
  chimera weasel --model anthropic/claude-sonnet-4 -p "..."
  chimera weasel --model google/gemini-2.5-pro -p "..."
  chimera weasel --model meta-llama/llama-3.3-70b -p "..."
  ```
- **Wired:** non-streaming `complete()` plus base-class shim for
  streaming. Tool calls forwarded as standard OpenAI deltas. No
  provider-side caching.

## Ollama (local)

Same `OllamaProvider` mink and otter use, same `/api/chat` endpoint,
same `keep_alive: 60m`, same `:cloud` tag handling. Weasel falls
back to Ollama automatically when no hosted key is set, which makes
the zero-config laptop story work:

```bash
brew install ollama
ollama serve &
ollama pull qwen3:32b
chimera weasel -p "explain this repo"   # picks qwen3:32b
```

- **Use:**
  ```bash
  chimera weasel --model qwen3:32b -p "summarize"
  chimera weasel --model glm-5.1:cloud -p "long-context refactor"
  chimera weasel --model kimi-k2.6:cloud -p "deep reasoning"
  ```
- **Wired:** native streaming over NDJSON, tool calls (`tool_calls`
  on `done:false` chunks), `think:true` for `kimi*` tags, per-request
  `num_ctx`, configurable `OLLAMA_HOST` for remote daemons.

## llama.cpp (`llama-server`)

Direct integration with `llama.cpp`'s OpenAI-compatible HTTP server.
Useful for running quantized GGUF models on the metal without going
through Ollama.

```bash
./llama-server -m ./models/qwen3-32b.Q4_K_M.gguf --port 8080
chimera weasel \
  --model qwen3-32b \
  --base-url http://localhost:8080/v1 \
  -p "list files"
```

- **Wired:** non-streaming + streaming chat completions, tool calls
  (when the model supports them), no caching, no thinking.
- The OpenAI-compatible adapter (`chimera/providers/compatible.py`)
  is what does the work; `--base-url` is the only extra flag.

## Modal

Modal-hosted vLLM container exposing OpenAI-shape `/v1/chat/completions`.
Weasel inherits the same adapter as mink — useful when you've stood
up an open-weight model on Modal.

- **Setup:**
  ```bash
  pip install modal httpx
  modal token new
  ```
- **Use:** `--model` only auto-routes Anthropic / OpenAI /
  OpenRouter / Ollama. To call Modal, build the provider in Python:
  ```python
  from chimera.providers import create_provider
  provider = create_provider(
      "modal", model="meta-llama/Llama-3.3-70B",
      base_url="https://your-org--llm-app-serve.modal.run/v1",
  )
  ```

## Custom providers

Implement `Provider` (`chimera/providers/base.py`) and call
`register_provider("my-name", factory)`
(`chimera/providers/registry.py`). Factory signature:
`factory(model=..., api_key=..., base_url=..., **kw)`. After
registration, `create_provider("my-name", model=...)` works
identically to the built-ins.

To make weasel pick your provider automatically:

1. Hand it an explicit model with a recognizable prefix (extend
   `_infer_provider`), or
2. Construct the provider yourself and pass it to the embedded SDK
   `Agent` constructor:
   ```python
   from chimera.weasel.sdk import Agent
   from chimera.providers import create_provider

   provider = create_provider("my-name", model="my-1.0")
   agent = Agent(provider=provider)
   ```

Self-registration on import (e.g. in your package `__init__.py`)
mirrors the built-ins.

## Choosing a provider

| Concern | Pick |
|---|---|
| Default, "just works" | `anthropic` (claude-sonnet-4-6) |
| One key, many vendors | `openrouter` (`anthropic/...`, `google/...`, `meta-llama/...`) |
| Privacy / local | `ollama` (qwen3:32b) or `llama.cpp` |
| Cheap + fast | `compatible` against Groq / DeepSeek / Together |
| Vision-heavy | `anthropic` (claude) or `openai` (gpt-4o) |
| Long context (>200k) | `google` (Gemini 1M), `anthropic` (200k), Kimi (262k) |
| Reasoning-tuned | `openai` (o3, o3-mini) or `anthropic` (claude-opus + thinking) |

## Mixing providers in one session

Weasel holds **one provider per process**. To swap mid-session, exit
and re-launch with a different `--model` or different env. The REPL
`/model` slash command cycles through the model list passed via
`--models <a>,<b>,<c>` — it rebuilds the provider on each switch.

In RPC mode, the host process can spawn a fresh `chimera weasel
--mode rpc` subprocess per provider it needs to drive.

## Auth storage

Weasel shares Chimera's credential storage with the rest of the CLI:

| Path | Source | Mode |
|---|---|---|
| `~/.chimera/credentials.json` | OAuth-issued tokens, refresh tokens | `0o600` |
| `~/.chimera/auth.json` | `AuthManager.set_token()` | default |

`CredentialStore._write` chmods to `0o600` after each save
(`chimera/auth/store.py`).

## Proxy mode for teams

For an org-wide gateway sitting in front of Anthropic-shaped
providers:

```bash
export ANTHROPIC_BASE_URL=http://proxy.internal:8000
export ANTHROPIC_AUTH_TOKEN=team-issued-jwt
chimera weasel --model claude-sonnet-4-6 -p "..."
```

`AnthropicProvider` honors both env vars. The dedicated `proxy`
provider (`chimera/providers/proxy.py`) is the alternative when your
gateway speaks its own JSON shape rather than the Anthropic wire
protocol.

## `--list-models`

Weasel ships a `--list-models` flag that asks each configured
provider for its catalogue and prints the union:

```bash
chimera weasel --list-models
chimera weasel --list-models --json | jq '.[] | select(.provider == "anthropic")'
```

The list is provider-driven (Anthropic and OpenAI return live
catalogues; Ollama returns installed tags) and updates every time
the CLI is launched — there's no static model file to keep current.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `weasel: no provider configured` | Set one of `$ANTHROPIC_API_KEY`, `$OPENAI_API_KEY`, `$OPENROUTER_API_KEY`, or pass `--model <id>` / `$WEASEL_MODEL`. Or `ollama serve`. |
| `401` / `403` | Wrong key. `printenv \| grep -E '(ANTHROPIC\|OPENAI\|OPENROUTER)_'` to verify. |
| OpenRouter not used despite key | Model id needs the `vendor/name` `/` separator. |
| `ImportError: pip install chimera-run[anthropic]` | `uv sync --extra anthropic` to pull the SDK. |
| `Cannot infer provider from model name '...'` | Pass `--model <id>` with a known prefix (`claude-*`, `gpt-*`, `gemini-*`, `glm-*`, `kimi-*`, `qwen*`, …). |
| Streaming hangs on first call | Cloud cold start. Anthropic / OpenAI typically warm in <2s; Ollama Cloud needs `keep_alive: 60m`. |
| `tool_calls` always empty on Ollama | You hit `/v1/chat/completions` instead of `/api/chat`. Set `OLLAMA_HOST` to the daemon root, not `.../v1`. |
| llama.cpp returns 404 | Confirm the OpenAI compat path. `llama-server` exposes `/v1/chat/completions` by default; pass that as the base URL. |

## See also

- [`modes.md`](modes.md) — provider behavior is identical across modes.
- [`docs/mink/providers.md`](../mink/providers.md) — line-numbered
  deep dive into every adapter.
- [`security-and-trademarks.md`](security-and-trademarks.md) — auth
  storage + redaction posture.

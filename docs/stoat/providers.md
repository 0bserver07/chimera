---
title: Stoat Providers
description: The Kimi-first provider chain, env vars, and how to override the default routing.
---

# Stoat Providers

`chimera stoat` resolves its provider through a single function —
`chimera.stoat.providers.build_provider` — used by every entry point:
the print path, the REPL, future RPC modes. The chain is **Kimi-first**
because the upstream shell-mode-toggle harness is tuned for Kimi K2.6
chat models served via the Moonshot OpenAI-compatible API.

## Resolution chain

First match wins:

| # | Trigger | Default model | Wire path |
|---|---|---|---|
| 1 | `--model <id>` on the CLI | (passed-through) | per-id inference |
| 2 | `$STOAT_MODEL` set | (env value) | per-id inference |
| 3 | `$MOONSHOT_API_KEY` set | `kimi-k2.6` | OpenAI-compatible @ `api.moonshot.ai/v1` |
| 4 | `$ANTHROPIC_API_KEY` set | `claude-sonnet-4-6` | Anthropic SDK |
| 5 | `$OPENAI_API_KEY` set | `gpt-4o` | OpenAI SDK |
| 6 | `$OPENROUTER_API_KEY` set | `moonshot/kimi-k2.6` | OpenAI-compatible @ `openrouter.ai/api/v1` |
| 7 | `$OLLAMA_API_KEY` set | `qwen3.5:cloud` | Ollama @ `127.0.0.1:11434` |
| 8 | none of the above | — | friendly `ValueError` |

Once the model id is resolved, stoat picks a wire path:

* Ids starting with `kimi-` (case-insensitive) route through the
  Moonshot OpenAI-compatible endpoint. `$MOONSHOT_API_KEY` is required
  on this path; missing it raises a friendly `ValueError`.
* Ids matching the OpenRouter `vendor/name` convention route through
  OpenRouter when `$OPENROUTER_API_KEY` is set.
* Ids matching the Ollama `name:tag` shape route through OllamaProvider.
* Otherwise we hand the id to the generic factory
  (`chimera.providers.factory.create_provider`), which infers Anthropic
  / OpenAI / Google by prefix.

You can call `chimera.stoat.providers.format_catalog()` to get the
chain rendered as `model<TAB>source` rows — handy for documenting your
deployment.

## Worked examples

### Kimi via Moonshot (default)

```bash
export MOONSHOT_API_KEY=...
chimera stoat -p "explain this repo"
```

Behind the scenes: `kimi-k2.6` resolved → wire path is
`compatible` (OpenAI chat completions) against
`https://api.moonshot.ai/v1`. Override the base URL with
`$MOONSHOT_BASE_URL` for self-hosted gateways.

### Anthropic fallback

```bash
unset MOONSHOT_API_KEY
export ANTHROPIC_API_KEY=sk-ant-...
chimera stoat -p "explain this repo"
```

`claude-sonnet-4-6` resolved → routed through the Anthropic SDK.

### Pinning via env

```bash
export STOAT_MODEL=gpt-4o
export OPENAI_API_KEY=sk-...
chimera stoat -p "..."
```

`STOAT_MODEL` short-circuits step 3 onwards; the resolved id (`gpt-4o`)
hands off to the generic factory (which routes via `openai`). Same flow
as `--model gpt-4o`.

### OpenRouter (vendor/name)

```bash
export OPENROUTER_API_KEY=sk-or-...
chimera stoat --model anthropic/claude-sonnet-4 -p "..."
```

The slash in the id triggers the OpenRouter wire path (OpenAI-compatible
against `openrouter.ai/api/v1`), with cosmetic `HTTP-Referer` and
`X-Title` headers set so the OpenRouter dashboard attributes the
traffic correctly.

### Local Ollama

```bash
export OLLAMA_API_KEY=ollama   # any non-empty value
chimera stoat --model qwen3.5:cloud -p "..."
```

The colon in the id triggers the Ollama wire path. Override
`$OLLAMA_HOST` for remote daemons.

## Configuring the Moonshot wire

The Moonshot endpoint is OpenAI-compatible; the same env vars that
apply to OpenAI generally apply here, except the base URL:

| Variable | Default | Meaning |
|---|---|---|
| `MOONSHOT_API_KEY` | (unset) | Required for the kimi-* path. |
| `MOONSHOT_BASE_URL` | `https://api.moonshot.ai/v1` | Override for self-hosted gateways. |
| `STOAT_MODEL` | (unset) | Pin a specific model id (e.g. `kimi-k2-thinking`). |

### Self-hosted Moonshot-compatible gateway

```bash
export MOONSHOT_API_KEY=$(cat /etc/stoat/moonshot-token)
export MOONSHOT_BASE_URL=https://gateway.internal/moonshot/v1
chimera stoat -p "..."
```

Stoat doesn't probe the gateway — it just builds the OpenAI-compatible
client and lets the wire-level errors surface naturally.

## Diagnosing chain misses

If `chimera stoat -p "..."` exits with `Error: stoat: no provider
configured.` the chain didn't find a usable env var. The error message
lists every supported variable with its default model. Either set one
or pass `--model <id>`.

If you see `stoat: $MOONSHOT_API_KEY is required for kimi-* models`,
you've selected a Kimi model (via `--model` or `$STOAT_MODEL`) but
the corresponding key isn't set. Either set the key or pick a different
model.

## See also

- `chimera/stoat/providers.py` — the resolution + wire-path source.
- [`quickstart.md`](quickstart.md) — install + first run.
- [`parity-matrix.md`](parity-matrix.md) — provider parity vs upstream.

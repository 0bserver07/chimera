---
title: "Use with Third-Party Providers"
description: "Use with Third-Party Providers"
---

Connect Chimera to any LLM backend -- cloud APIs, local servers, or
OpenAI-compatible endpoints -- by setting a few environment variables or
passing arguments to `create_provider()`.

---

## How Provider Detection Works

`chimera.create_provider()` resolves a provider in this order:

1. **Explicit `provider_type`** -- if you pass `"anthropic"`, `"openai"`,
   `"ollama"`, `"compatible"`, etc., that is used directly.
2. **Model name prefix** -- `claude-*` maps to Anthropic, `gpt-*` / `o1-*` /
   `o3-*` to OpenAI, `gemini-*` to Google, `llama*` / `mistral*` / `qwen*` /
   `phi*` to Ollama.
3. **Provider catalog** -- checks the built-in model catalog.
4. **Environment variables** -- if `ANTHROPIC_BASE_URL` or
   `ANTHROPIC_AUTH_TOKEN` is set, falls back to the Anthropic provider. If
   `OPENAI_API_KEY` is set, falls back to OpenAI.

The model name itself comes from the `model=` argument, or from the
`ANTHROPIC_MODEL` / `OPENAI_MODEL` environment variable when omitted.

---

## GLM-5 via api.z.ai

Set the three environment variables that the Anthropic provider reads:

```bash
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_AUTH_TOKEN="your-token"
export ANTHROPIC_MODEL="glm-5"
```

Then create a provider with zero arguments:

```python
import chimera

provider = chimera.create_provider()  # picks up all three env vars
```

Or pass the model explicitly and let the env vars supply the rest:

```python
provider = chimera.create_provider(model="glm-5")
```

Because `glm-5` does not match any known prefix, the factory sees
`ANTHROPIC_BASE_URL` in the environment and routes to the Anthropic provider
with the custom base URL.

---

## OpenRouter

OpenRouter exposes an Anthropic-compatible API, so the same env vars apply:

```bash
export ANTHROPIC_BASE_URL="https://openrouter.ai/api"
export ANTHROPIC_AUTH_TOKEN="your-openrouter-key"
export ANTHROPIC_MODEL="anthropic/claude-sonnet-4-20250514"
```

```python
import chimera

provider = chimera.create_provider()
```

Model names use OpenRouter's `provider/model` format (e.g.
`google/gemini-2.0-flash`, `meta-llama/llama-3-70b`). Any model listed on
OpenRouter works -- the Anthropic provider forwards the name as-is.
OpenRouter handles failover and load balancing on their side.

---

## vLLM / Local OpenAI-Compatible Endpoints

For servers that expose `/v1/chat/completions` (vLLM, LiteLLM, Together,
Fireworks, Groq), use the `compatible` provider type:

```python
import chimera

provider = chimera.create_provider(
    "compatible",
    model="my-local-model",
    base_url="http://localhost:8000/v1",
)
```

`base_url` is required for `compatible`. An optional `api_key` can be passed
if the server requires authentication:

```python
provider = chimera.create_provider(
    "compatible",
    model="meta-llama/Llama-3-70B",
    base_url="https://api.together.xyz/v1",
    api_key="your-together-key",
)
```

---

## Ollama

Models whose names start with `llama`, `mistral`, `qwen`, or `phi` are
auto-detected as Ollama:

```python
import chimera

provider = chimera.create_provider(model="llama3.2")
```

For other model names, specify the provider type explicitly:

```python
provider = chimera.create_provider(
    "ollama",
    model="deepseek-r1",
    base_url="http://localhost:11434",
)
```

The default base URL is `http://localhost:11434`, so you can omit it when
Ollama runs on the standard port.

---

## Using .env Files

Keep credentials out of your shell history by storing them in a `.env` file
at the project root:

```bash
# .env
ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
ANTHROPIC_AUTH_TOKEN="your-token"
ANTHROPIC_MODEL="glm-5"
```

Source it before running Chimera:

```bash
source .env
chimera code
```

:::tip
`.env` is included in `.gitignore` by default. Never commit API keys.
:::---

## CLI Usage

All provider settings work with `chimera code` and every other subcommand.
The CLI reads the same environment variables:

```bash
export ANTHROPIC_BASE_URL="https://openrouter.ai/api"
export ANTHROPIC_AUTH_TOKEN="your-key"
chimera code --model anthropic/claude-sonnet-4-20250514
```

The `--model` flag overrides `ANTHROPIC_MODEL`. Everything else is picked up
from the environment.

---

## Next Steps

- [Build a Coding Agent](/build-a-coding-agent/) -- use providers
  programmatically inside an agent loop.
- [Use the REPL](/use-the-repl/) -- interactive session with any provider.

---
title: Mink Model Compatibility
description: Smoke-tested Ollama models for the chimera mink CLI, with measured cold-start, tool dispatch, and cost.
---

# Mink Model Compatibility

This page lists every Ollama model that has been end-to-end smoke-tested against
`chimera mink` on this machine, along with the captured run IDs that produced
the numbers. Every row is reproducible from the eventlog directories cited.

The two smokes used are identical for every model:

| Smoke | Purpose | Command (paraphrased) |
| --- | --- | --- |
| **A: text-only** | proves provider wiring | `mink -p "Say hello in three words." --max-steps 1` |
| **B: tool dispatch** | proves the model emits a real `tool_calls` block that mink can route to the bash tool | `mink -p "Use the bash tool to run 'echo HELLO_FROM_<MODEL>' and report the result." --max-steps 4` |

Both invoked with `--permission-mode bypassPermissions --output-format json`.
"Tool calls" below refers to `tool_calls_total` from the per-run
`~/.chimera/eventlog/<run_id>/summary.json`. Wall-clock is `ended_at -
started_at` from the same file. Costs are whatever `OllamaProvider` reported
back to the run summary; cloud models that the local Ollama daemon does not
price out come back as `$0.0000` (the cost is borne by the upstream account,
not surfaced through `/api/chat`).

## Recommended models

| Model | Kind | Context (default) | Tool calls observed | Smoke A turn | Smoke B turn | Cost / turn (B) | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `glm-5.1:cloud` | cloud | 131072 | 1 | 30.0s | 17.0s | $0.0204 | Validated working baseline. Slowest cold-start in this batch (30s on first call). |
| `glm-5:cloud` | cloud | 131072 | 1 | 1.0s | 9.0s | $0.0214 | Fastest text response, dispatches tools cleanly. Cheap-ish per turn. |
| `kimi-k2.5:cloud` | cloud | 131072 | 1 | 4.0s | 17.0s | $0.0000 | Reasoning model (`think:true` auto-enabled by provider). Steady tool use. |
| `kimi-k2.6:cloud` | cloud | 131072 | 1 | 9.0s | 11.0s | $0.0000 | Default mink model. Reasoning enabled by default. |
| `minimax-m2:cloud` | cloud | 131072 | 1 | 1.0s | 9.0s | $0.0000 | Tied for fastest tool turn. |
| `minimax-m2.7:cloud` | cloud | 131072 | 1 | 2.0s | 10.0s | $0.0000 | Successor of m2; same tool-use behavior. |
| `qwen3.5:cloud` | cloud | 131072 | 1 | 3.0s | 10.0s | $0.0000 | Clean tool dispatch, fast. |
| `gpt-oss:120b-cloud` | cloud | 131072 | 1 | 1.0s | 9.0s | $0.0000 | Tied for fastest tool turn. One transient 500 from `/api/chat` on first try; succeeded on retry. |
| `llama3.2:3b` | local | 131072 | 1 | 22.0s (text smoke failed)* | 49.0s | $0.0000 | Smallest viable local model. Slow and unreliable on free-form text smoke; surprisingly does dispatch the bash tool. |

*Smoke A returned `success=false` ("Max steps reached") for `llama3.2:3b`. See
"Models that failed or are partial" below.

## Per-model details

Setup commands assume Ollama is running at `http://localhost:11434`. Cloud
tagged models (`*:cloud`) are manifest-only pulls (a few hundred bytes each)
and require `ollama signin` to be authenticated against an account that has
access to the cloud model catalog. Local models are full weight downloads.

### glm-5.1:cloud

- **Setup:** `ollama signin` (once), then `ollama pull glm-5.1:cloud`.
- **Strengths:** Stable, accurate tool dispatch, tracks the prompt closely.
- **Weaknesses:** Saw a 30s cold-start on the first text request of the
  session; subsequent calls are sub-10s.
- **Suggested flags:** `--max-steps 50` (default), `--tool-timeout 120`.
- **Evidence:**
  - Smoke A: `~/.chimera/eventlog/mink-20260425T182550-523915ec/summary.json`
  - Smoke B: `~/.chimera/eventlog/mink-20260425T183450-71742dd6/summary.json`

### glm-5:cloud

- **Setup:** `ollama pull glm-5:cloud`.
- **Strengths:** Fastest text response in this batch (1.0s), good tool
  formatting.
- **Weaknesses:** Slightly higher per-turn cost surfaced through the
  provider ($0.0214 on smoke B).
- **Suggested flags:** `--max-steps 50`, `--tool-timeout 120`.
- **Evidence:**
  - Smoke A: `~/.chimera/eventlog/mink-20260425T182630-1a0e2683/summary.json`
  - Smoke B: `~/.chimera/eventlog/mink-20260425T183512-430cc00a/summary.json`

### kimi-k2.5:cloud

- **Setup:** `ollama pull kimi-k2.5:cloud`.
- **Strengths:** Reasoning model; the Ollama provider auto-sets `think:true`
  for any tag starting with `kimi`. Good for multi-step planning.
- **Weaknesses:** ~17s on smoke B is mid-pack; reasoning tokens add latency.
- **Suggested flags:** `--max-steps 50`, `--tool-timeout 180` (give reasoning
  room).
- **Evidence:**
  - Smoke A: `~/.chimera/eventlog/mink-20260425T182631-dce51214/summary.json`
  - Smoke B: `~/.chimera/eventlog/mink-20260425T183521-d621617c/summary.json`

### kimi-k2.6:cloud

- **Setup:** `ollama pull kimi-k2.6:cloud`. This is the **mink default**
  (see `_DEFAULT_MODEL` in `chimera/mink/cli.py`).
- **Strengths:** Reasoning enabled by default; well-tuned for the Chimera
  loop; this is the model the rest of the project benchmarks against.
- **Weaknesses:** Cold-start can exceed 2 minutes on a freshly-signed-in
  account (not observed in this run because the account was already warm).
- **Suggested flags:** defaults are tuned for it. `--tool-timeout 300` if
  you expect a cold cloud node.
- **Evidence:**
  - Smoke A: `~/.chimera/eventlog/mink-20260425T182651-acb8b23a/summary.json`
  - Smoke B: `~/.chimera/eventlog/mink-20260425T183616-d60db3fd/summary.json`

### minimax-m2:cloud

- **Setup:** `ollama pull minimax-m2:cloud`.
- **Strengths:** Tied for fastest tool turn (9.0s on smoke B).
- **Weaknesses:** None observed in smoke; weights are older than m2.7.
- **Suggested flags:** `--max-steps 50`, `--tool-timeout 120`.
- **Evidence:**
  - Smoke A: `~/.chimera/eventlog/mink-20260425T182704-975eb417/summary.json`
  - Smoke B: `~/.chimera/eventlog/mink-20260425T183645-fec8628f/summary.json`

### minimax-m2.7:cloud

- **Setup:** `ollama pull minimax-m2.7:cloud`.
- **Strengths:** Newer minimax weights; tool dispatch is clean, runs fast.
- **Weaknesses:** None observed in smoke.
- **Suggested flags:** `--max-steps 50`, `--tool-timeout 120`.
- **Evidence:**
  - Smoke A: `~/.chimera/eventlog/mink-20260425T182641-5e4c98b3/summary.json`
  - Smoke B: `~/.chimera/eventlog/mink-20260425T183544-9f4db463/summary.json`

### qwen3.5:cloud

- **Setup:** `ollama pull qwen3.5:cloud`.
- **Strengths:** Fast, clean tool dispatch, very chatty in a good way.
- **Weaknesses:** None observed in smoke.
- **Suggested flags:** `--max-steps 50`, `--tool-timeout 120`.
- **Evidence:**
  - Smoke A: `~/.chimera/eventlog/mink-20260425T182643-5be3897c/summary.json`
  - Smoke B: `~/.chimera/eventlog/mink-20260425T183554-bb818c61/summary.json`

### gpt-oss:120b-cloud

- **Setup:** `ollama pull gpt-oss:120b-cloud`.
- **Strengths:** Tied for fastest tool turn (9.0s). Useful as a third-party
  baseline for tool-use comparison.
- **Weaknesses:** Returned a 500 from `/api/chat` on the first smoke-B
  attempt (cold cloud node, transient); succeeded on the immediate retry.
  Plan for a one-shot retry around this model.
- **Suggested flags:** `--max-steps 50`, `--tool-timeout 180`. Wrap calls in
  retry-on-500 if you script against it.
- **Evidence:**
  - Smoke A: `~/.chimera/eventlog/mink-20260425T182650-c027e2e2/summary.json`
  - Smoke B (retry): `~/.chimera/eventlog/mink-20260425T183635-7391c9e5/summary.json`

### llama3.2:3b (local)

- **Setup:** `ollama pull llama3.2:3b`.
- **Strengths:** Pure local, zero cost, surprisingly does dispatch the bash
  tool (smoke B succeeded).
- **Weaknesses:** Free-form smoke A failed with `success=false` and
  `"Max steps reached"` after 22s — at `--max-steps 1` the 3B model spent
  the budget without producing a stop-able message. Smoke B took 49s, the
  slowest in the entire batch.
- **Suggested flags:** Use as a fallback only. `--max-steps 6`,
  `--tool-timeout 120`. Do **not** rely on it for production tool loops.
- **Evidence:**
  - Smoke A (failed): `~/.chimera/eventlog/mink-20260425T183421-c1be4b5d/summary.json`
  - Smoke B (passed): `~/.chimera/eventlog/mink-20260425T183920-8e864999/summary.json`

## Models that failed or are partial

| Model | Status | Reason |
| --- | --- | --- |
| `glm-4.7-flash` (local, 19 GB) | **failed** | First-load `httpx.ReadTimeout` even at a 480s overall timeout. The 19GB MoE weights take longer than the default httpx read window to spin up on this hardware. Would need a custom Modelfile that pre-warms with `keep_alive` raised, or a longer client read timeout in `OllamaProvider`. |
| `qwen3.5` (local, non-cloud tag) | **failed to pull** | `Error: pull model manifest: 412 ... requires a newer version of Ollama.` Upgrade Ollama via `https://ollama.com/download` to use this tag. The cloud variant `qwen3.5:cloud` works fine. |
| `llama3.2:3b` (local) | **partial** | Smoke A failed (`Max steps reached`); smoke B passed but at 49s. Treat as fallback only. |

## How to add your own model

The Ollama provider lives at
[`chimera/providers/ollama.py`](https://github.com/0bserver07/chimera/blob/master/chimera/providers/ollama.py).
Notable defaults:

- `_DEFAULT_NUM_CTX = 131_072` (131k context window pushed to Ollama as
  `num_ctx`).
- `_DEFAULT_KEEP_ALIVE = "60m"` (model stays warm for an hour after the last
  call).
- `think=True` is auto-enabled when the model tag starts with `kimi`.
  Override per-call by passing `think=...` through provider kwargs.

To register a custom Ollama model:

1. Create a Modelfile pointing at your base weights and any system-prompt
   tuning. Example: `FROM kimi-k2.6:cloud` plus your own `SYSTEM`.
2. `ollama create my-agent -f ./Modelfile`.
3. `chimera mink --model my-agent ...`. The provider will dispatch through
   the same `/api/chat` path, so tool-calling parity is preserved.

For a fully custom provider (e.g., raise `num_ctx`, change `keep_alive`,
disable thinking on a kimi tag), instantiate `OllamaProvider` directly in
your own script:

```python
from chimera.providers.ollama import OllamaProvider

provider = OllamaProvider(
    model="kimi-k2.6:cloud",
    context_length=200_000,
    keep_alive="2h",
    think=False,
)
```

## Switching models

There are two supported ways to switch the model mink talks to:

1. **Per-invocation via `--model`:**

   ```bash
   chimera mink --model glm-5:cloud -p "..."
   chimera mink --model minimax-m2.7:cloud -p "..."
   ```

2. **Per-environment fallback via `CHIMERA_MINK_FALLBACK`:**
   `chimera mink` first probes `/api/tags` for the requested `--model`. If the
   tag is not present locally and `CHIMERA_MINK_FALLBACK` is set, it falls
   back to that tag instead of failing. Default fallback is `qwen3:32b` (see
   `chimera/mink/cli.py`).

   ```bash
   export CHIMERA_MINK_FALLBACK=glm-5.1:cloud
   chimera mink --model some-not-yet-pulled-model -p "..."
   # -> uses glm-5.1:cloud
   ```

   The legacy `CHIMERA_CC_FALLBACK` is still accepted with a deprecation
   warning.

There is **no** `CHIMERA_MINK_MODEL` env var; the default model is hardcoded
in `_DEFAULT_MODEL` in `chimera/mink/cli.py` (currently `kimi-k2.6:cloud`).
If you want a session-wide default, alias it:

```bash
alias mink="chimera mink --model glm-5.1:cloud"
```

## Catalog refresh — wave 13 (2026-05)

The default `ProviderCatalog` (`chimera/providers/catalog.py`) ships
explicit `ModelConfig` bindings for the seven model families below.
Routing inference (`chimera/providers/factory.py:_infer_provider`) is
covered by `tests/providers/test_catalog_refresh.py`.

| Model id | Provider | Endpoint / env | Pricing (in/out per Mtok) | Notes |
| --- | --- | --- | --- | --- |
| `qwen3-coder` | ollama | `$OLLAMA_HOST` | `$0` / `$0` | Local Ollama tag (Alibaba). DashScope API path: pass `provider_type="compatible"` + `base_url=https://dashscope-intl.aliyuncs.com/compatible-mode/v1`. |
| `qwen3-coder-30b` | ollama | `$OLLAMA_HOST` | `$0` / `$0` | Same family, 30B coder. |
| `qwen3-32b` | ollama | `$OLLAMA_HOST` | `$0` / `$0` | Same family, 32B general. |
| `glm-4.6` | anthropic | `https://api.z.ai/api/anthropic` + `$ANTHROPIC_AUTH_TOKEN` | `$0.6` / `$2.2` *(placeholder)* | Zhipu Anthropic-compat. TODO: confirm rates against [docs.z.ai](https://docs.z.ai/api-reference/llm/chat-completion). |
| `glm-5.1` | anthropic | `https://api.z.ai/api/anthropic` + `$ANTHROPIC_AUTH_TOKEN` | `$2` / `$8` *(mirrors glm-5)* | Same endpoint as glm-5; pricing TODO until Zhipu publishes 5.1 sheet. |
| `deepseek-v3.1-terminus` | compatible | `https://api.deepseek.com/v1` + `$DEEPSEEK_API_KEY` | `$0.27` / `$1.10` *(placeholder)* | DeepSeek hosted OpenAI-compat. |
| `deepseek-coder-v3` | compatible | `https://api.deepseek.com/v1` + `$DEEPSEEK_API_KEY` | `$0.27` / `$1.10` *(placeholder)* | Coder line; longest-prefix matched ahead of `deepseek-chat`. |
| `gpt-oss-120b` | ollama | `$OLLAMA_HOST` | `$0` / `$0` | OpenAI open weights via Ollama. Routed to ollama by an explicit `gpt-oss` prefix that fires *before* the `gpt-*` → OpenAI rule. |
| `gpt-oss-20b` | ollama | `$OLLAMA_HOST` | `$0` / `$0` | Smaller OSS sibling. |
| `kimi-k2-0905-preview` | anthropic | `https://api.moonshot.ai/anthropic` + `$MOONSHOT_API_KEY` | `$0.6` / `$2.5` *(placeholder)* | Moonshot Anthropic-compat. `:cloud` Kimi tags stay served by Ollama. |
| `kimi-k2.5` | anthropic | `https://api.moonshot.ai/anthropic` + `$MOONSHOT_API_KEY` | `$0.6` / `$2.5` *(placeholder)* | Same endpoint, k2.5 GA line. |
| `mistral-codestral-2511` | ollama | `$OLLAMA_HOST` | `$0` / `$0` | Mistral coder. For Mistral hosted API, override with `provider_type="compatible"` + `base_url=https://api.mistral.ai/v1` + `$MISTRAL_API_KEY`. |
| `gemma3-27b-instruct` | ollama | `$OLLAMA_HOST` | `$0` / `$0` | Google open weights. Routed via a new `gemma` prefix; hosted Gemini stays on the `gemini-*` → Google branch. |

Pricing entries flagged *(placeholder)* are educated guesses — refresh
once the upstream vendor publishes per-SKU rates. Local Ollama tags
report `$0` because `/api/chat` does not surface a price field; the
real cost is hardware + electricity.

## Reproducing this report

Every smoke in this report was run against a live Ollama daemon at
`http://localhost:11434` from this checkout. To reproduce a single row:

```bash
# Smoke A
timeout 90 uv run python -m chimera.cli.main mink \
  --model <MODEL> --permission-mode bypassPermissions --output-format json \
  -p "Say hello in three words." --max-steps 1

# Smoke B
timeout 180 uv run python -m chimera.cli.main mink \
  --model <MODEL> --permission-mode bypassPermissions --output-format json \
  -p "Use the bash tool to run 'echo HELLO_FROM_<TAG>' and report the result." \
  --max-steps 4
```

Each invocation prints a `[mink] run saved as mink-<timestamp>-<id> at
~/.chimera/eventlog/mink-<timestamp>-<id>/` line; the `summary.json` in that
directory carries `tool_calls_total`, `cost_usd`, `started_at`, `ended_at`,
and `success` — the same fields cited above.

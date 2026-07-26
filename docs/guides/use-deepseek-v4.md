---
title: "Use DeepSeek-V4"
description: "Drive Chimera against DeepSeek-V4 — direct API, Ollama cloud passthrough, and local Ollama hosting."
---

DeepSeek-V4 is wired into Chimera's model catalog with three transports:

| Model id | Transport | Endpoint |
|---|---|---|
| `deepseek-v4` | OpenAI-compatible | `https://api.deepseek.com/v1` |
| `deepseek-v4-pro` | OpenAI-compatible | `https://api.deepseek.com/v1` |
| `deepseek-v4-pro:cloud` | Ollama | `$OLLAMA_HOST` (cloud passthrough) |

The bare ids hit DeepSeek's hosted OpenAI-compatible endpoint. The `:cloud`-tagged id hits Ollama's `ollama run deepseek-v4-pro:cloud` cloud passthrough — useful when you already have an Ollama signin and prefer the unified endpoint. You can also pull the model into a **local** Ollama daemon for fully offline use.

Context window: 128k tokens for every variant.

## Prerequisites

Pick one of the three paths below.

### Direct API

- A DeepSeek API key — [api-docs.deepseek.com](https://api-docs.deepseek.com)
- `export DEEPSEEK_API_KEY=sk-...`

### Ollama cloud

- Ollama 0.6+ — [ollama.com/download](https://ollama.com/download)
- An Ollama account: `ollama signin`
- `export OLLAMA_HOST=https://ollama.com` (default)

### Local Ollama

- Ollama 0.6+
- Sufficient RAM and storage to host the model locally — `ollama pull deepseek-v4-pro` first

All three paths require Chimera installed:

```bash
pip install chimera-run
```

## Quickstart: direct API

```python
import os
from chimera.providers.factory import create_provider

os.environ["DEEPSEEK_API_KEY"] = "sk-..."

provider = create_provider(model="deepseek-v4-pro")
response = provider.complete("Write a Python one-liner that reverses a list.")
print(response.content)
```

The factory resolves `deepseek-v4` / `deepseek-v4-pro` / `deepseek-chat` / `deepseek-reasoner` to the OpenAI-compatible provider pointed at `https://api.deepseek.com/v1` with the `DEEPSEEK_API_KEY` env var.

## Quickstart: Ollama cloud passthrough

```bash
ollama signin
export OLLAMA_HOST=https://ollama.com
```

```python
from chimera.providers.factory import create_provider

provider = create_provider(model="deepseek-v4-pro:cloud")
response = provider.complete("Refactor this function for clarity.")
print(response.content)
```

The `:cloud` suffix forces the Ollama transport regardless of any prefix matching elsewhere in the catalog. The factory inspects the trailing `:cloud` (and friends like `glm-5.1:cloud`, `kimi-k2.6:cloud`) to short-circuit to the Ollama provider.

## Quickstart: local Ollama

```bash
ollama pull deepseek-v4-pro
ollama serve  # if not already running on :11434
```

```python
import os
from chimera.providers.factory import create_provider

os.environ["OLLAMA_HOST"] = "http://localhost:11434"

provider = create_provider(model="deepseek-v4-pro")
# When OLLAMA_HOST points at a local daemon, the factory will route through
# the local Ollama transport instead of the public DeepSeek API.
response = provider.complete("Implement a binary search.")
print(response.content)
```

For full offline operation, drop `DEEPSEEK_API_KEY` from your environment so the factory cannot accidentally fall back to the hosted API.

## Driving a coding agent

```python
import asyncio
from chimera.assembly.coding_agent import CodingAgent

agent = CodingAgent(model="deepseek-v4-pro")

async def main():
    result = await agent.arun(
        "Add a CLI flag --json to scripts/format_report.py and write a test."
    )
    print(result.output)

asyncio.run(main())
```

`CodingAgent` wires up the default tools, a ReAct loop, and the right environment for the workspace. The `model=` kwarg flows through `create_provider`.

## CLI examples

```bash
# Synthesize against the direct API
chimera synthesize "Build a calculator REST API" --tests tests/ --model deepseek-v4-pro

# Eval HumanEval against the cloud passthrough
chimera eval --benchmark humaneval --model deepseek-v4-pro:cloud --limit 10

# Otter REPL against the direct API
otter chat --model deepseek-v4

# Code REPL with model cycling: try DeepSeek-V4 first, then fall back to GLM-5
chimera code --models deepseek-v4-pro,glm-5
```

## Pricing notes

The cost catalog carries DeepSeek's published V4 rates, verified 2026-07-25 against
the [pricing page](https://api-docs.deepseek.com/quick_start/pricing): **`deepseek-v4-flash`
$0.14 / $0.28** and **`deepseek-v4-pro` $0.435 / $0.87** per Mtok (cache-miss input;
DeepSeek's cache-hit input rate is ~50x lower and is not modelled, so cache-heavy
workloads are over-billed here, never under). `deepseek-chat` and `deepseek-reasoner`
are deprecated aliases for v4-flash's non-thinking and thinking modes and share its rate.

> These were previously placeholders copied from `deepseek-reasoner` ($0.55 / $2.19).
> The placeholder outlived DeepSeek's rate-card publication because the entries were
> marked as deliberate overrides, which silenced the pricing auditor — see
> `PRICING_PLACEHOLDERS` in `chimera/providers/cost.py`.

## Troubleshooting

- **`No API key found for deepseek-v4-pro`** — set `DEEPSEEK_API_KEY` (or use the `:cloud` variant for Ollama).
- **`OLLAMA_HOST is unreachable`** — start `ollama serve`, or set `OLLAMA_HOST=https://ollama.com` for cloud.
- **`Model 'deepseek-v4-foo' not in catalog`** — only `deepseek-v4`, `deepseek-v4-pro`, `deepseek-v4-pro:cloud` are wired. Custom variants need a `ModelConfig` registered against `chimera.providers.catalog`.
- **Slow context-window saturation** — every V4 variant ships at 128k context. If you're hitting it on long sessions, enable `chimera.compaction.thresholds.ThresholdCompaction` in your `LoopConfig`.

## See also

- [Use with Ollama](./use-with-ollama) — broader Ollama setup, including local Qwen and other cloud models.
- [Use with third-party providers](./use-with-third-party-providers) — the general OpenAI-compatible recipe DeepSeek follows.

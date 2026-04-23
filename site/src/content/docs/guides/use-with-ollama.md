---
title: "Use with Ollama"
description: "Drive Chimera agents using Ollama's Anthropic-compatible API — local Qwen or cloud Kimi/GLM."
---

Point Chimera at Ollama's Anthropic-compatible endpoint and run the full agent against a local Qwen model or a hosted cloud model with zero code changes.

Local models are free and private. Cloud models are capable enough for real coding work. Either way, there is nothing to install on the Chimera side beyond what you already have.

---

## Prerequisites

- Ollama 0.6 or later — [ollama.com/download](https://ollama.com/download)
- For cloud models: an Ollama account — [ollama.com](https://ollama.com)
- For local models: at least 16 GB RAM and a model with a 64k-token context window
- Chimera installed: `pip install "git+https://github.com/0bserver07/chimera.git#egg=chimera-run"`

---

## Configure the Endpoint

Ollama serves the Anthropic API shape at `http://localhost:11434`. Set these three environment variables so Chimera's Anthropic provider routes through Ollama:

```bash
export ANTHROPIC_BASE_URL="http://localhost:11434"
export ANTHROPIC_AUTH_TOKEN="ollama"
export ANTHROPIC_API_KEY=""
```

The `ANTHROPIC_AUTH_TOKEN` value is a placeholder — Ollama does not validate it for local requests, but Chimera's provider expects the variable to be set. For cloud models, sign in with `ollama signin` first.

Chimera agents need at least 64k tokens of context to operate reliably. Pick a model whose listed context window meets that bar.

---

## Recommended Models

| Model | Hosting | Context | Best for |
|-------|---------|---------|----------|
| `kimi-k2.6:cloud` | Cloud | 200k+ | Full coding agent, tool use, long sessions |
| `glm-5.1:cloud` | Cloud | 128k | Coding and refactoring |
| `qwen3.5:cloud` | Cloud | 128k | General-purpose, fast |
| `minimax-m2.7:cloud` | Cloud | 1M+ | Very long contexts |
| `glm-4.7-flash` | Local | 128k | Fast local coding, no API costs |
| `qwen3.5` | Local | 128k | Local general-purpose work |

Browse the full cloud catalog at [ollama.com/search?c=cloud](https://ollama.com/search?c=cloud). Verify the context window on the model's page before choosing — numbers above are typical at time of writing.

---

## Quickstart: Text Completion

```python
import os
import chimera

os.environ["ANTHROPIC_BASE_URL"] = "http://localhost:11434"
os.environ["ANTHROPIC_AUTH_TOKEN"] = "ollama"

provider = chimera.create_provider(model="kimi-k2.6:cloud")
response = provider.complete("Write a Python one-liner that reverses a list.")
print(response.content)
```

---

## Quickstart: Full Coding Agent

```python
import asyncio
import os
from chimera.assembly.coding_agent import CodingAgent

os.environ["ANTHROPIC_BASE_URL"] = "http://localhost:11434"
os.environ["ANTHROPIC_AUTH_TOKEN"] = "ollama"

agent = CodingAgent(model="kimi-k2.6:cloud")

async def main():
    async for event in agent.run("Create hello.py that prints 'hi from ollama'."):
        print(event.type.value, getattr(event.data, "content", "")[:100])

asyncio.run(main())
```

---

## Running the Examples

Two runnable scripts live in the repo:

- `examples/ollama_quickstart.py` — minimal provider call
- `examples/ollama_coding_agent.py` — full agent loop on a trivial task

Set the env vars above, then run either script.

---

## Context Window Note

Chimera (and Claude Code) assume at least 64k tokens of context. Many smaller local models ship with 4k or 8k defaults. Check the model card, and if Ollama exposes a `num_ctx` parameter for your model, set it when pulling or serving so the value matches what the model actually supports.

A model that advertises 128k but runs with a 4k effective context will truncate tool results silently and cause the agent to loop or lose state.

---

## Troubleshooting

- **`ValueError: Cannot infer provider`** — set `ANTHROPIC_BASE_URL` so Chimera's provider detection picks the Anthropic route.
- **`Connection refused`** — Ollama is not running. Start it with `ollama serve`.
- **`model not found`** — pull the model first: `ollama pull kimi-k2.6:cloud`.
- **Tool calls fail silently or return empty** — some smaller local models have weak tool-use. Switch to `kimi-k2.6:cloud` or `glm-5.1:cloud` before debugging your own code.
- **Agent truncates mid-task** — the model's effective context is below 64k. Pick a larger model or raise `num_ctx`.

---

## What Works, What Doesn't

Chimera's Anthropic provider talks to Ollama's Anthropic-compatible endpoint. Cloud models like `kimi-k2.6:cloud` and `glm-5.1:cloud` work end-to-end with the full coding agent — tools, streaming, compaction, everything. Local models work for inference but tool-use quality varies by model; pick one that advertises function-calling support.

A separate native Ollama provider exists in Chimera for non-Anthropic-compatible use cases (raw `/api/generate` and `/api/chat`). For the coding agent, the Anthropic-compatible path is simpler and gets you streaming and tool use out of the box.

If a model does not support tool use at all, the agent will fail on its first tool call. There is no graceful fallback — choose a tool-capable model.

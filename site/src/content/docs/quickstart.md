---
title: "Quickstart"
description: "Get Chimera running in 5 minutes"
---

## Install

```bash
pip install chimera-ai
# or with uv
uv add chimera-ai
```

For provider-specific extras:

```bash
pip install chimera-ai[anthropic]  # Claude
pip install chimera-ai[openai]     # GPT
pip install chimera-ai[google]     # Gemini
```

## Set up credentials

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# or for compatible endpoints:
export ANTHROPIC_BASE_URL="https://api.example.com"
export ANTHROPIC_AUTH_TOKEN="your-token"
export ANTHROPIC_MODEL="your-model"
```

## Your first agent

```python
import chimera

provider = chimera.create_provider()
agent = chimera.Agent(provider=provider)
result = agent.run("What is 2 + 2?", env=None)
print(result.output)
```

## Synthesize code

```python
import chimera

result = chimera.synthesize(
    spec="Create a function that checks if a number is prime",
    tests="python -m pytest tests/",
    workdir="./my-project",
)
print(f"Converged: {result.converged} in {result.iterations} iterations")
```

## Use a preset

```python
import chimera

provider = chimera.create_provider()

# SWE-Agent style (retry loop, minimal tools)
agent = chimera.AgentPreset.SWE_AGENT.build(provider)

# Aider style (lint feedback loop)
agent = chimera.AgentPreset.AIDER.build(provider)

# Cline style (plan then execute)
agent = chimera.AgentPreset.CLINE.build(provider)

# Codex style (full tool suite)
agent = chimera.AgentPreset.CODEX.build(provider)
```

## Next steps

- [Architecture](/architecture/) — understand the 8-layer stack
- [Build a Coding Agent](/guides/build-a-coding-agent/) — custom agent composition
- [API Reference](/reference/core/) — full module docs

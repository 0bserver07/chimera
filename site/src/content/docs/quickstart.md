---
title: "Quickstart"
description: "Get Chimera running in 5 minutes"
---

## Install

```bash
pip install chimera-run
# or with uv
uv add chimera-run
```

For provider-specific extras:

```bash
pip install chimera-run[anthropic]  # Claude
pip install chimera-run[openai]     # GPT
pip install chimera-run[google]     # Gemini
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

The canonical, fully-assembled stack is `CodingAgent.from_preset(...)`:

```python
from chimera.assembly.coding_agent import CodingAgent

# SWE-bench style (minimal scaffold, root-cause focus)
agent = CodingAgent.from_preset("swebench")

# Codex style (full tool suite)
agent = CodingAgent.from_preset("codex")

# Kimi style (action-first, KISS)
agent = CodingAgent.from_preset("kimi")

# Default full-featured coding agent
agent = CodingAgent.from_preset("coding_agent")
```

> The legacy `chimera.AgentPreset.SWE_AGENT.build(provider)` family
> emits a `DeprecationWarning` and will be removed in v0.7.0. See
> [Migration: AgentPreset → CodingAgent](/chimera/migrations/v0.4-to-v0.5/#agentpresetbuild--codingagentfrom_preset).

## The 7-CLI tour

Chimera ships seven coding-agent CLIs, each composed from the same
`CodingAgent` library but with different per-CLI defaults. The fastest
way to pick one is the discovery commands:

```bash
# Show all 7 codenames + aliases + inspirations + parity row
chimera agents

# Recommend a CLI from a free-text description of what you want to do
chimera which "I want a TUI with mid-turn steering"
chimera which "host a long-running agent for multiple clients"

# Diagnose your environment (API keys, daemons, optional extras)
chimera doctor
```

Each CLI is invoked as a subcommand, with a short alias for the posture
it adopts:

```bash
chimera mink           # alias: tui     — TUI-first interactive
chimera otter          # alias: multi   — server-first, HTTP+SSE / ACP serve
chimera ferret         # alias: sandbox — sandbox × approval composition
chimera weasel         # alias: mini    — minimal harness, four modes
chimera shrew          # alias: tiny    — small local model tuning
chimera stoat          # alias: shell   — shell-mode toggle, Kimi defaults
chimera badger         # alias: strict  — harness-rewrite + parity tracking
```

`chimera resume <id>` auto-detects which CLI minted a session id and
hands the resume off to the right CLI; `chimera resume` (no id) picks
the most recent run across all seven.

## Next steps

- [Architecture](/chimera/architecture/) — understand the 9-phase stack and how the seven CLIs compose
- [Agents concept](/chimera/concepts/agents/) — `Agent` vs `CodingAgent`, presets, the 7-CLI architecture
- [Build a Coding Agent](/chimera/guides/build-a-coding-agent/) — custom agent composition
- [API Reference](/chimera/reference/core/) — full module docs

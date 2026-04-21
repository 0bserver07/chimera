---
title: "Getting Started"
description: "Getting Started"
---

## Install

```bash
pip install chimera-run                  # core (zero dependencies)
pip install chimera-run[anthropic]       # + Claude support
pip install chimera-run[openai]          # + OpenAI support
pip install chimera-run[all]             # all providers
```

Requires **Python 3.11+**.

---

## Provider Setup

Chimera auto-detects the provider from the model name. Set the appropriate
environment variables for your backend:

### Anthropic (Claude)
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### OpenAI
```bash
export OPENAI_API_KEY="sk-..."
```

### Google (Gemini)
```bash
export GOOGLE_API_KEY="..."
```

### Ollama (local)
No credentials needed -- Chimera connects to `http://localhost:11434`
by default.

### Anthropic-compatible (e.g. GLM-5)
```bash
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_AUTH_TOKEN="your-token-here"
export ANTHROPIC_MODEL="glm-5"
```

:::tip[Explicit provider creation]
You can skip environment variables entirely and pass credentials in code:

```python
provider = chimera.create_provider(
    "anthropic",
    model="glm-5",
    base_url="https://api.z.ai/api/anthropic",
    api_key="...",
)
```
:::

### Supported Providers

| Provider | Model Prefixes | Auto-detected |
|----------|---------------|---------------|
| Anthropic | `claude-*` | Yes |
| OpenAI | `gpt-*`, `o1-*`, `o3-*` | Yes |
| Google | `gemini-*` | Yes |
| Ollama | `llama-*`, `mistral-*`, `qwen-*`, `phi-*` | Yes |
| Modal | -- | No (`provider_type="modal"`) |
| Compatible | -- | No (`provider_type="compatible"`) |

---

## Your First Agent

This example creates an agent with the default tool set, points it at a local
workspace directory, and asks it to generate a Python script.

```python
import chimera

# 1. Create a provider (auto-detects from env vars)
provider = chimera.create_provider()

# 2. Build an agent with the built-in tools
agent = chimera.Agent(
    provider=provider,
    tools=list(chimera.DEFAULT_TOOLS),
)

# 3. Run the agent in a local environment
env = chimera.LocalEnvironment("./workspace")
env.setup()

result = agent.run(
    "Create a hello world Python script",
    env=env,
)

print(result.output)
env.cleanup()
```

:::note[What happens under the hood]
The agent enters a **ReAct loop** -- it thinks, picks a tool (e.g.
`write_file`), observes the result, and repeats until the task is done or
the step limit is reached. Every tool call is recorded in `result.steps`.
:::---

## Direct Provider Usage

If you only need LLM completions (no agent loop), use the provider directly:

```python
import chimera

provider = chimera.create_provider()
response = provider.complete([chimera.Message.user("What is 2+2?")])
print(response.content)        # "4"
print(response.usage)          # {"input_tokens": 12, "output_tokens": 1}
```

---

## Tool Use

Attach tool schemas to a provider call and let the model decide when to invoke
them:

```python
import chimera

provider = chimera.create_provider()

tool = {
    "name": "calculator",
    "description": "Evaluate a math expression.",
    "input_schema": {
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
}

response = provider.complete(
    [chimera.Message.user("What is 137 * 42? Use the calculator.")],
    tools=[tool],
)

if response.has_tool_calls:
    tc = response.tool_calls[0]
    print(f"{tc.name}({tc.arguments})")  # calculator({'expression': '137 * 42'})
```

---

## One-Liner Synthesis

Synthesize an entire codebase from a prompt and test suite:

```python
import chimera

result = chimera.synthesize(
    "Build a REST API for tasks",
    tests="./tests/",
    # uses ANTHROPIC_MODEL env var, or pass model="glm-5" explicitly
)
print(f"Converged: {result.converged}, Cost: ${result.total_cost:.4f}")
```

---

## Configured Synthesis

For full control, wire up every component:

```python
import chimera

trainer = chimera.Trainer(
    spec=chimera.Spec.from_tests("./tests/", "Build a task manager"),
    agent=chimera.Agent(
        provider=chimera.create_provider(),
        tools=list(chimera.DEFAULT_TOOLS),
        loop=chimera.ReAct(max_steps=50),
    ),
)
result = trainer.synthesize(strategy=chimera.TestConvergence(max_epochs=10))
```

:::tip[Strategies]
Chimera ships with several synthesis strategies beyond `TestConvergence`:
`CurriculumStrategy`, `EnsembleStrategy`, `Passthrough`, `TreeSearch`,
`MajorityVoting`, and `AIMOEnsemble`. See the
[Training concepts](/concepts/training/) page for details.
:::---

## Run the Examples

Runnable scripts live in `examples/`:

```bash
# Test provider connection (text, tool use, multi-turn)
python examples/quickstart_provider.py --model glm-5

# Synthesize a calculator from tests (end-to-end)
python examples/quickstart_synthesize.py --model glm-5
```

---

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key | `sk-ant-...` |
| `ANTHROPIC_AUTH_TOKEN` | Alternative auth token (for compatible endpoints) | `0d141d...` |
| `ANTHROPIC_BASE_URL` | Base URL for Anthropic-compatible APIs | `https://api.z.ai/api/anthropic` |
| `ANTHROPIC_MODEL` | Default model name (used by examples) | `glm-5` |
| `OPENAI_API_KEY` | OpenAI API key | `sk-...` |
| `GOOGLE_API_KEY` | Google Gemini API key | `AI...` |

---

## Next Steps

- **[Core Concepts](/concepts/agents/)** -- understand agents, providers, tools, loops, environments, and the training layer.
- **[Build a Coding Agent](/guides/build-a-coding-agent/)** -- step-by-step guide to building a production agent.
- **[Modules](/modules/acp/)** -- events, compaction, detection, permissions, streaming, sessions, auth.
- **[API Reference](/reference/core/)** -- full reference for every public class and function.

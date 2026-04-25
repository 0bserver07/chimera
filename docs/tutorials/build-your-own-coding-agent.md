# Build Your Own Coding Agent in 50 Lines

This tutorial walks you through building a working coding agent — one that reads files, edits code, runs tests, and uses bash — using Chimera. By the end you'll have a CLI tool you can point at any repo.

## Prerequisites

```bash
git clone https://github.com/0bserver07/chimera && cd chimera
uv sync --extra dev --extra anthropic

# Set up your LLM provider
export ANTHROPIC_API_KEY="sk-ant-..."  # or any Anthropic-compatible endpoint
```

## Step 1: The Minimal Agent (4 lines)

```python
# my_agent.py
import chimera

provider = chimera.create_provider()
agent = chimera.Agent(provider=provider, tools=list(chimera.AGENT_TOOLS))
result = agent.run("What files are in this directory?", env=chimera.LocalEnvironment("."))
print(result.output)
```

Run it:

```bash
python my_agent.py
```

That's a working agent. It has 15 tools (file read/write/edit, bash, search, git, test, web search, etc.), a ReAct loop, and auto-detected provider. But it's one-shot — let's make it interactive.

## Step 2: Interactive REPL (15 lines)

```python
# my_repl.py
import chimera

provider = chimera.create_provider()
env = chimera.LocalEnvironment(".")
env.setup()

agent = chimera.Agent(
    provider=provider,
    tools=list(chimera.AGENT_TOOLS),
    loop=chimera.ReAct(max_steps=30),
)

while True:
    task = input("\n> ").strip()
    if task in ("exit", "quit"):
        break
    result = agent.run(task, env=env)
    print(result.output)

env.cleanup()
```

Now you have a conversational coding agent. Ask it to fix bugs, write tests, refactor code.

## Step 3: Add Infrastructure (35 lines)

A real coding agent needs cost tracking, event monitoring, and undo:

```python
# my_coding_agent.py
import chimera

provider = chimera.create_provider()
env = chimera.LocalEnvironment(".")
env.setup()

# Infrastructure
event_bus = chimera.EventBus()
event_bus.subscribe("tool_call", lambda e: print(f"  [{e.metadata.get('tool_name', '?')}]"))
cost_tracker = chimera.CostTracker()
ghost = chimera.GhostCommitManager(".")

config = chimera.LoopConfig(
    event_bus=event_bus,
    cost_tracker=cost_tracker,
    ghost_commits=ghost,
    truncation=chimera.TruncationConfig(max_lines=200),
)

agent = chimera.Agent(
    provider=provider,
    tools=list(chimera.AGENT_TOOLS),
    loop=chimera.ReAct(max_steps=30, config=config),
    prompt=chimera.Prompt.from_string(
        "You are an expert coding agent. Read code before editing. "
        "Run tests after changes. Be precise and minimal."
    ),
)

while True:
    task = input("\n> ").strip()
    if task == "exit":
        break
    if task == "undo":
        print(ghost.undo())
        continue
    if task == "cost":
        print(f"${cost_tracker.total_cost:.4f}")
        continue
    result = agent.run(task, env=env)
    print(result.output)

env.cleanup()
```

Now you have:
- Live tool call display (see what the agent does in real-time)
- Cost tracking (know how much each task costs)
- Undo (revert the last edit with "undo")
- Output truncation (long bash output won't flood the context)

## Step 4: Use a Preset (4 lines)

Instead of configuring everything, use a preset that matches a known agent:

```python
import chimera

provider = chimera.create_provider()

# SWE-Agent style: retry loop, minimal tools
agent = chimera.AgentPreset.SWE_AGENT.build(provider)

# Aider style: lint feedback loop, catches syntax errors
agent = chimera.AgentPreset.AIDER.build(provider)

# Cline style: plan first (read-only), then execute
agent = chimera.AgentPreset.CLINE.build(provider)

# Codex style: full tool suite
agent = chimera.AgentPreset.CODEX.build(provider)
```

## Step 5: Compose Your Own Architecture

Mix and match primitives to create something new:

```python
import chimera
from chimera.core.loops.retry import RetryLoop
from chimera.core.loops.lint_feedback import LintFeedbackLoop

provider = chimera.create_provider()

# Your custom loop: retry with lint checking
inner = chimera.ReAct(max_steps=20)
lint_loop = LintFeedbackLoop(inner=inner, linter="ruff", max_rounds=2)
retry_loop = RetryLoop(inner=lint_loop, max_retries=3)

agent = chimera.Agent(
    provider=provider,
    tools=list(chimera.AGENT_TOOLS),
    loop=retry_loop,
)

result = agent.run("Fix the bug in auth.py", env=chimera.LocalEnvironment("."))
```

That's a retry loop wrapping a lint feedback loop wrapping a ReAct loop. If the agent's fix has lint errors, it fixes them. If the fix still doesn't work, it retries from scratch. Three levels of resilience, composed from modular blocks.

## What's Next

- [Architecture](../architecture.md) — understand the 8-layer stack
- [Modules](../modules/) — every component documented
- [Examples](../../examples/) — 39 runnable scripts
- [Benchmarks](../benchmarks/README.md) — HumanEval 90.9%, SWE-bench transparency

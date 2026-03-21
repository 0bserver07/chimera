# Chimera

Compose agents from providers, tools, loops, and environments. The same primitives that make up Claude Code or Codex — ReAct loops, tool execution, context management — are modular blocks you can swap and reconfigure. A working coding agent is ~50 lines of Python.

**Status: Alpha** — 2503 tests, 8 agent architectures replicable, benchmarked on HumanEval (90.9%) and SWE-bench.

## Try It

```bash
git clone https://github.com/0bserver07/chimera && cd chimera
uv sync --extra dev --extra anthropic
export ANTHROPIC_API_KEY="sk-ant-..."   # or any Anthropic-compatible endpoint
python -m chimera code --workdir .      # interactive coding agent
```

## 4-Line Agent

```python
import chimera

provider = chimera.create_provider()
agent = chimera.Agent(provider=provider, tools=list(chimera.AGENT_TOOLS))
result = agent.run("Fix the failing test in auth.py", env=chimera.LocalEnvironment("."))
```

## Quick Start

```python
import chimera

# One-liner: synthesize code from a spec
result = chimera.synthesize("Build a REST API for tasks", tests="./tests/")

# Configured: choose your loop and tools
agent = chimera.Agent(
    provider=chimera.create_provider(),
    tools=list(chimera.AGENT_TOOLS),
    loop=chimera.ReAct(max_steps=50),
)
result = agent.run("Write unit tests for utils.py", env=chimera.LocalEnvironment("."))

# Preset: replicate any coding agent in one line
agent = chimera.AgentPreset.SWE_AGENT.build(provider)   # retry loop + minimal tools
agent = chimera.AgentPreset.AIDER.build(provider)        # lint feedback loop
agent = chimera.AgentPreset.CLINE.build(provider)        # plan-then-act
agent = chimera.AgentPreset.CODEX.build(provider)        # full tool suite
```

## Architecture

8-layer stack. Each layer usable independently:

```
Layer 8  CLI             chimera synthesize / eval / bench / code / review / ci-fix
Layer 7  Workflows       CIFix, Review, Research, Migration, DocGen, TestGen
Layer 6  Synthesis       Trainer, Strategy, Spec, Architecture, Constraint
Layer 5  Evaluation      Harness, Metrics, Benchmarks (SWE-bench, HumanEval, AIMO)
Layer 4  Agent           Agent, Tools, Loops, Prompt, Context, Critic, ACP
Layer 3  Provider        Anthropic, OpenAI, Google, Ollama, Modal, OpenAI-compat
Layer 2  Infrastructure  Security, Secrets, Events, Sessions, Compaction, MCP, LSP
Layer 1  Environment     Local, Docker, Git, Remote, Cloud, PersistentShell
```

## Benchmarks

| Benchmark | Score | Model |
|-----------|-------|-------|
| HumanEval (164 problems) | **90.9% pass@1** | GLM-5 |
| Terminal-Bench (10 tasks) | **30%** | GLM-5 |
| SWE-bench Lite (20 instances) | **10%** | GLM-5 |

[Full transparency report](docs/benchmarks/README.md) with 13 tracked issues.

## Why Chimera, Not Claude Code/Codex?

**Use Claude Code or Codex** if you want a finished product that works today.

**Use Chimera** if you want to:
- **Understand** how coding agents work — every major agent's architecture decomposed into primitives
- **Build custom agents** — compose your own loops, tools, and strategies without forking someone else's monolith
- **Research** agent architectures — benchmark framework with full transparency
- **Prototype fast** — go from idea to working agent in 50 lines, not 5000

Chimera is a framework, not a product. It's early — the building blocks exist, the community decides what to build with them.

## Philosophy

Chimera treats agentic coding as a machine learning problem. When an engineer writes a spec, agents iterate on code, and the result passes all tests — that's training. The spec is the loss function. The agent loop is the optimizer. The core verb is `.synthesize()`.

## Links

- [Tutorial: Build a Claude Code-Like Agent](docs/tutorials/build-your-own-claude-code.md) — 50 lines, step by step
- [Documentation](https://chimera.run) — full docs site
- [Getting Started](docs/getting-started.md) — provider setup, first agent
- [Examples](examples/) — 39 runnable scripts
- [Benchmarks](docs/benchmarks/README.md) — transparency framework
- [Contributing](CONTRIBUTING.md) — setup, workflow, code style
- [Changelog](CHANGELOG.md) — version history

## License

[AGPL-3.0](LICENSE)

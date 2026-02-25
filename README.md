# Chimera

A composable coding agent framework. Synthesize codebases from specifications.

**Status: Alpha** — Core framework complete (448 tests passing). API may change before 1.0.

## Quick Start

Chimera offers three levels of control:

```python
# One-liner — specify what you want, get a codebase
result = chimera.synthesize("Build a REST API for tasks", tests="./tests/")

# Configured — choose your provider, strategy, and constraints
trainer = chimera.Trainer(
    architecture=chimera.Architecture(layers=[
        chimera.Layer("api", deps=[]),
        chimera.Layer("db", deps=["api"]),
    ]),
    spec=chimera.Spec.from_tests("./tests/", "Build a task manager"),
    agent=chimera.Agent(provider=chimera.create_provider("claude-sonnet-4-20250514")),
)
result = trainer.synthesize(strategy=chimera.TestConvergence(max_epochs=10))

# Framework-author — subclass and customize everything
class MyAgent(chimera.Agent):
    tools = chimera.DEFAULT_TOOLS
    loop = chimera.ReAct(max_steps=50)

class MyStrategy(chimera.Strategy):
    def run(self, agent, spec, env, constraints=None, callbacks=None):
        # Your custom synthesis loop here
        ...
```

## Install

```bash
pip install chimera-ai                  # core (zero dependencies)
pip install chimera-ai[anthropic]       # + Claude support
pip install chimera-ai[openai]          # + OpenAI support
pip install chimera-ai[all]             # all providers
```

Requires Python 3.11+.

## Architecture

Chimera is a 6-layer stack. Each layer can be used independently or composed:

```
Layer 6: CLI           chimera synthesize / chimera eval / chimera bench
Layer 5: Synthesis     Trainer, Strategy, Spec, Architecture, Constraint
Layer 4: Evaluation    Harness, Metrics, AntiOverfit, Benchmarks
Layer 3: Agent         Agent, Tools, Loops, Prompt, Context
Layer 2: Provider      Claude, GPT, Gemini, Ollama, OpenAI-compatible
Layer 1: Environment   Local, Docker, Git, persistent shell (tmux)
```

Use Layer 1-3 as an agent toolkit. Use Layer 1-5 as a synthesis framework. Use Layer 6 from the command line.

## Features

**Zero-dependency core.** Optional extras for providers — no bloated dependency tree.

**5 LLM providers.** Anthropic, OpenAI, Google Gemini, Ollama, any OpenAI-compatible endpoint (OpenRouter, vLLM, Groq). Auto-detected via `create_provider()`.

**12 built-in tools.** File read/write/edit/search/list, bash, test runner, git, web fetch, regex replace, sub-agent delegation.

**4 loop types.** ReAct (default), PlanAndExecute, Reflexion, TreeOfThought.

**3 composition patterns.** Pipeline (sequential), Ensemble (parallel + selector), Supervisor (coordinator + workers).

**5 training strategies.** TestConvergence (default — iterate until tests pass), TreeSearch (parallel branch exploration), Curriculum (topological ordering), Ensemble (multiple attempts), Passthrough (single-shot).

**Evaluation harness.** Benchmark adapters for SWE-bench, HumanEval, and custom benchmarks. Metrics: pass@k, resolve rate, average cost. Anti-overfit detection.

**Persistent shell.** tmux-based session management for stateful shell operations across agent steps.

**Cost tracking.** Per-model pricing, real cost aggregation through all loop types, budget limits via callbacks.

**Repository mapping.** aider-style structural overview — classes, functions, and imports extracted via AST.

**CLI.** `chimera synthesize`, `chimera eval`, `chimera bench` — run from the terminal.

## Philosophy

Chimera treats agentic coding as a machine learning problem. The insight (from the observation): when an engineer writes a spec, agents iterate on code, and the result is a codebase that passes all tests — that's training. The spec is the loss function. The agent loop is the optimizer. The output is a trained model you deploy without inspecting its internals.

The core verb is `.synthesize()` — program synthesis, not chat.

## Roadmap

- [x] ~~Cost tracking~~ (done)
- [x] ~~`chimera.synthesize()` one-liner~~ (done)
- [x] ~~Tree search strategy~~ (done)
- [x] ~~Repository mapping~~ (done)
- [ ] Real provider integration tests (with API keys)
- [ ] Docker environment integration tests
- [ ] Plugin/extension system
- [ ] Multi-file edit transactions
- [ ] Documentation site

## Contributing

```bash
git clone https://github.com/your-username/chimera.git
cd chimera
pip install -e ".[dev]"
python -m pytest
```

The project uses TDD. Tests go in `tests/`, one file per module. Run `ruff check` for linting and `mypy chimera/` for type checking.

## License

AGPL-3.0

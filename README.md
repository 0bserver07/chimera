# Chimera

A composable coding agent framework. Synthesize codebases from specifications.

**Status: Alpha** -- Core framework complete (1700+ tests passing). API may change before 1.0.

## Quick Start

Chimera offers three levels of control:

```python
# One-liner -- specify what you want, get a codebase
result = chimera.synthesize("Build a REST API for tasks", tests="./tests/")

# Configured -- choose your provider, strategy, and constraints
trainer = chimera.Trainer(
    architecture=chimera.Architecture(layers=[
        chimera.Layer("api", deps=[]),
        chimera.Layer("db", deps=["api"]),
    ]),
    spec=chimera.Spec.from_tests("./tests/", "Build a task manager"),
    agent=chimera.Agent(provider=chimera.create_provider("claude-sonnet-4-20250514")),
)
result = trainer.synthesize(strategy=chimera.TestConvergence(max_epochs=10))

# Framework-author -- subclass and customize everything
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

## Provider Setup

Chimera works with any Anthropic-compatible API. Configure via environment variables:

```bash
# GLM-5 via api.z.ai
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_AUTH_TOKEN="your-token-here"

# Or Claude direct
export ANTHROPIC_API_KEY="sk-ant-..."

# Or OpenAI
export OPENAI_API_KEY="sk-..."
```

The provider auto-detects from model name. For unknown models (e.g. `glm-5`), it falls back to checking environment variables.

```python
# Auto-detected from env
provider = chimera.create_provider(model="glm-5")

# Explicit
provider = chimera.create_provider("anthropic", model="glm-5",
    base_url="https://api.z.ai/api/anthropic", api_key="...")
```

See [docs/getting-started.md](docs/getting-started.md) for full configuration reference and [examples/](examples/) for runnable scripts.

## Architecture

Chimera is an 8-layer stack. Each layer can be used independently or composed:

```
Layer 8: CLI             chimera synthesize / eval / bench / code / review /
                         ci-fix / research / docs / testgen / migrate / plugins
Layer 7: Workflows       CIFixWorkflow, ReviewOrchestrator, Researcher,
                         MigrationPlanner, DocGenerator, TestGenerator
Layer 6: Synthesis       Trainer, Strategy, Spec, Architecture, Constraint
Layer 5: Evaluation      Harness, Metrics, Benchmarks (SWE-bench, HumanEval, AIMO)
Layer 4: Agent           Agent, Tools, Loops, Prompt, Context, Critic, ACP
Layer 3: Provider        Anthropic, OpenAI, Google, Ollama, Modal, OpenAI-compat
Layer 2: Infrastructure  Security, Secrets, Permissions, Events, Sessions,
                         Compaction, Streaming, Detection, Config, Plugins, MCP, LSP
Layer 1: Environment     Local, Docker, Git, Remote, Cloud, PersistentShell
```

Use Layer 1-4 as an agent toolkit. Use Layer 1-6 as a synthesis framework. Use Layer 7-8 from the command line.

## Features

**Zero-dependency core.** Optional extras for providers -- no bloated dependency tree.

**6 LLM providers.** Anthropic, OpenAI, Google Gemini, Ollama, Modal, any OpenAI-compatible endpoint (OpenRouter, vLLM, Groq, GLM-5). Auto-detected via `create_provider()`.

**16 built-in tools.** File read/write/edit/search/list, bash, test runner, git, web fetch, regex replace, sub-agent delegation, answer verification, repo map, image read, browser, import graph.

**4 loop types.** ReAct (default), PlanAndExecute, Reflexion, TreeOfThought.

**3 composition patterns.** Pipeline (sequential), Ensemble (parallel + selector), Supervisor (coordinator + workers).

**7 training strategies.** TestConvergence (default -- iterate until tests pass), TreeSearch (parallel branch exploration), Curriculum (topological ordering), Ensemble (multiple attempts), MajorityVoting (pass@N consensus), AIMOEnsemble (voting + tree search fallback), Passthrough (single-shot).

**6 environments.** Local, Docker, Git (branch isolation), Remote (HTTP client), Cloud (managed sandbox), PersistentShell (tmux sessions).

**6 workflows.** CIFixWorkflow (parse CI logs, fix, retry), ReviewOrchestrator (reviewer + author iteration), Researcher (plan decomposition + synthesis), MigrationPlanner (rule-based transforms with presets), DocGenerator (AST-based documentation), TestGenerator (source analysis + test skeletons).

**Evaluation harness.** Benchmark adapters for SWE-bench, HumanEval, AIMO, and custom benchmarks. Metrics: pass@k, resolve rate, average cost.

**Security.** RiskClassifier for tool calls, LLM and rule-based security analyzers, confirmation policies (NeverConfirm, AlwaysConfirm, ConfirmAboveThreshold).

**Secrets.** SecretDetector with 10 built-in patterns (API keys, AWS, Bearer, private keys, etc.), RedactionMiddleware for event streams.

**Critic.** In-loop action evaluation with LLMCritic (provider-based) and ChecklistCritic (rule-based). Configurable for all-actions or finish-only mode.

**ACP.** Agent Client Protocol for external agent interop -- JSON-RPC 2.0 over subprocess stdio, wrapping external agents as Chimera tools.

**Plugins.** Plugin lifecycle management, extension registry (agents, strategies, constraints, middleware, skills, MCP, hooks), directory loader, marketplace (search, install, uninstall).

**Interactive REPL.** `chimera code` with 14 slash commands: /help, /model, /cost, /clear, /history, /tools, /context, /debug, /session, /compact, /audit, /checkpoint, /agent, /exit.

**MCP and LSP.** Model Context Protocol client (stdio/HTTP) for tool sources. Language Server Protocol integration for diagnostics, completion, and rename.

**Cost tracking.** Granular token tracking (cache, reasoning, per-step breakdown) with per-model pricing and budget limits.

**Sessions.** Multi-turn conversation persistence with Memory, File, and SQLite backends. Event-sourced persistence with append-only log, file locking, crash recovery, and gap detection.

**Checkpoints.** Named checkpoints with create, restore (by name or ID), undo, and list operations.

**Persistent shell.** tmux-based session management for stateful shell operations across agent steps.

**Repository mapping.** aider-style structural overview -- classes, functions, and imports extracted via AST.

## Philosophy

Chimera treats agentic coding as a machine learning problem. The insight (from the observation): when an engineer writes a spec, agents iterate on code, and the result is a codebase that passes all tests -- that's training. The spec is the loss function. The agent loop is the optimizer. The output is a trained model you deploy without inspecting its internals.

The core verb is `.synthesize()` -- program synthesis, not chat.

## Roadmap

- [x] ~~Cost tracking~~ (done)
- [x] ~~`chimera.synthesize()` one-liner~~ (done)
- [x] ~~Tree search strategy~~ (done)
- [x] ~~Repository mapping~~ (done)
- [x] ~~Real provider integration tests~~ (done -- 12 tests, any Anthropic-compatible endpoint)
- [x] ~~Plugin/extension system~~ (done)
- [x] ~~Documentation site~~ (done)
- [x] ~~CI agent~~ (done -- CIFixWorkflow)
- [ ] Docker environment integration tests

## Contributing

```bash
git clone https://github.com/your-username/chimera.git
cd chimera
pip install -e ".[dev]"
python -m pytest
```

The project uses TDD. Tests go in `tests/`, one file per module. Run `ruff check chimera/` for linting and `mypy chimera/` for type checking.

### Integration tests

Run real provider tests with credentials:

```bash
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_AUTH_TOKEN="your-token"
export ANTHROPIC_MODEL="glm-5"
python -m pytest tests/test_provider_anthropic_integration.py -v
```

These tests are skipped automatically when no credentials are set.

## License

AGPL-3.0

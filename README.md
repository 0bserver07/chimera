# Chimera

Compose agents from providers, tools, loops, and environments. The same primitives that make up Claude Code or Codex — ReAct loops, tool execution, context management — are modular blocks you can swap and reconfigure. A working coding agent is ~50 lines of Python.

**Status: Alpha** — 2459 tests, 8 agent architectures replicable, benchmarked on HumanEval (90.9%) and SWE-bench. API may change before 1.0.

## What You Can Build

```bash
# A coding agent -- like a mini Claude Code / Codex, in 150 lines:
source .env
python examples/coding_agent.py "Build a REST API with Flask" --workdir /tmp/project
python examples/coding_agent.py "Fix the failing test" --workdir .
python examples/coding_agent.py "Review this code" --workdir .
python examples/coding_agent.py -i --workdir .  # interactive REPL
```

```python
# Or programmatically:
import chimera

provider = chimera.create_provider(model="glm-5")
agent = chimera.Agent(provider=provider, tools=list(chimera.AGENT_TOOLS))
result = agent.run("Write unit tests for utils.py", env=env)
```

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
    agent=chimera.Agent(provider=chimera.create_provider()),  # auto-detects from env
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
uv add chimera-ai                       # core (zero dependencies)
uv add chimera-ai[anthropic]            # + Claude support
uv add chimera-ai[openai]              # + OpenAI support
uv add chimera-ai[all]                 # all providers
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

## Examples

All verified against real GLM-5. Run with `source .env` first:

| Example | What it does |
|---------|-------------|
| `examples/coding_agent.py` | Full coding agent -- one-shot tasks or interactive REPL |
| `examples/agent_with_tools.py` | Agent creates files and runs them (13 tools) |
| `examples/composition_pipeline.py` | Chain agents: coder -> reviewer |
| `examples/think_and_ask.py` | Agent reasons internally + asks user questions |
| `examples/wire_monitoring.py` | Real-time monitoring of agent steps via Wire |
| `examples/dmail_context_rewind.py` | Agent rewinds its own context to save tokens |
| `examples/flow_skills.py` | Guide an agent through a Mermaid decision tree |
| `examples/quickstart_synthesize.py` | Generate code from test specifications |
| `examples/synthesis_with_diagnostics.py` | Synthesis with training curves + complexity constraint |
| `examples/cegis_synthesis.py` | CEGIS — counterexample-guided synthesis |
| `examples/sketch_synthesis.py` | Sketch — fill holes in partial code |
| `examples/validation_split.py` | Validation split — detect overfitting |

```bash
python examples/run_all.py        # interactive menu
python examples/run_all.py 1      # run a specific example
python examples/run_all.py all    # run everything
```

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
Layer 2: Infrastructure  Security, Secrets, Permissions, Events, Sessions, Wire,
                         Compaction, Streaming, Detection, Config, Plugins, MCP, LSP
Layer 1: Environment     Local, Docker, Git, Remote, Cloud, PersistentShell
```

Use Layer 1-4 as an agent toolkit. Use Layer 1-6 as a synthesis framework. Use Layer 7-8 from the command line.

## Features

**Zero-dependency core.** Optional extras for providers -- no bloated dependency tree.

**6 LLM providers.** Anthropic, OpenAI, Google Gemini, Ollama, Modal, any OpenAI-compatible endpoint (OpenRouter, vLLM, Groq, GLM-5). Auto-detected via `create_provider()`.

**20 built-in tools.** File read/write/edit/search/list, bash, test runner, git, web fetch, regex replace, sub-agent delegation, answer verification, repo map, image read, browser, import graph, think (reasoning scratchpad), ask_user (pause for input), todo (task tracking), dmail (context rewind). `AGENT_TOOLS` preset gives coding agents 13 tools out of the box.

**7 loop types.** ReAct (default), PlanAndExecute, Reflexion, TreeOfThought, RetryLoop (retry + scoring wrapper), PlanActLoop (read-only planning then full execution), LintFeedbackLoop (run linter after edits, feed errors back).

**3 composition patterns.** Pipeline (sequential), Ensemble (parallel + selector), Supervisor (coordinator + workers).

**9 training strategies.** TestConvergence (default -- iterate until tests pass), TreeSearch (parallel branch exploration), Curriculum (topological ordering), Ensemble (multiple attempts), MajorityVoting (pass@N consensus), AIMOEnsemble (voting + tree search fallback), Passthrough (single-shot), CEGISStrategy (counterexample-guided -- one failing test at a time), IncrementalStrategy (targeted re-synthesis of failing functions only).

**6 environments.** Local, Docker, Git (branch isolation), Remote (HTTP client), Cloud (managed sandbox), PersistentShell (tmux sessions).

**6 workflows.** CIFixWorkflow (parse CI logs, fix, retry), ReviewOrchestrator (reviewer + author iteration), Researcher (plan decomposition + synthesis), MigrationPlanner (rule-based transforms with presets), DocGenerator (AST-based documentation), TestGenerator (source analysis + test skeletons).

**Evaluation harness.** Benchmark adapters for SWE-bench, HumanEval, AIMO, and custom benchmarks. Metrics: pass@k, resolve rate, average cost.

**Security.** RiskClassifier for tool calls, LLM and rule-based security analyzers, confirmation policies (NeverConfirm, AlwaysConfirm, ConfirmAboveThreshold).

**Secrets.** SecretDetector with 10 built-in patterns (API keys, AWS, Bearer, private keys, etc.), RedactionMiddleware for event streams.

**Critic.** In-loop action evaluation with LLMCritic (provider-based) and ChecklistCritic (rule-based). Configurable for all-actions or finish-only mode.

**ACP.** Agent Client Protocol for external agent interop -- JSON-RPC 2.0 over subprocess stdio, wrapping external agents as Chimera tools.

**Plugins.** Plugin lifecycle management, extension registry (agents, strategies, constraints, middleware, skills, MCP, hooks), directory loader, marketplace (search, install, uninstall).

**Interactive REPL.** `chimera code` with 16 slash commands: /help, /model, /cost, /clear, /history, /tools, /context, /debug, /session, /compact, /audit, /checkpoint, /agent, /init, /yolo, /exit.

**MCP and LSP.** Model Context Protocol client (stdio/HTTP) for tool sources. Language Server Protocol integration for diagnostics, completion, and rename.

**Cost tracking.** Granular token tracking (cache, reasoning, per-step breakdown) with per-model pricing and budget limits.

**Sessions.** Multi-turn conversation persistence with Memory, File, and SQLite backends. Event-sourced persistence with append-only log, file locking, crash recovery, and gap detection.

**Checkpoints.** Named checkpoints with create, restore (by name or ID), undo, and list operations.

**Persistent shell.** tmux-based session management for stateful shell operations across agent steps.

**Repository mapping.** aider-style structural overview -- classes, functions, and imports extracted via AST. Multi-language support: Python, TypeScript, Go, Rust.

**Wire protocol.** Bidirectional agent-UI communication channel with fire-and-forget and request/response patterns. Real-time step lifecycle events (TurnBegin/End, StepBegin/End), tool status updates, approval request/response.

**D-Mail.** Context rewind -- agent creates checkpoints during a conversation, then "sends a message to its past self" to truncate context back to a checkpoint with only the useful findings. Inspired by Kimi CLI.

**Flow Skills.** Parse Mermaid flowcharts into executable decision trees. Convert to agent prompts with current position tracking, advance through flows with choice selection.

## Agent Replication

Chimera can replicate the architecture of any major coding agent by composing its primitives:

| Agent | Chimera Primitives Used |
|-------|----------------------|
| SWE-Agent | RetryLoop + SWE_TOOLS + DemonstrationPrompt + trajectory logging |
| Aider | LintFeedbackLoop + TreeSitter + GitEnvironment + RepoMap + commit style inference |
| Cline | PlanActLoop + FocusChain + DefinitionLookup + InstructionLayer |
| Codex CLI | SandboxPolicy + GhostCommits + head+tail truncation + response caching |
| OpenHands | AgentController FSM + microagents + ACP + Critic + EventBus |
| Gemini CLI | GroundedSearch + ContextCache + thought stripping + subagent investigator |
| OpenCode | LSP feedback middleware + SemanticSearch + ApplyMiddleware |
| Kimi CLI | Wire protocol + DMailTool + Flow skills + ThinkTool |

```python
# One-liner presets
agent = chimera.AgentPreset.SWE_AGENT.build(provider)
agent = chimera.AgentPreset.AIDER.build(provider)
agent = chimera.AgentPreset.CLINE.build(provider)
agent = chimera.AgentPreset.CODEX.build(provider)
```

## Benchmarks

| Benchmark | Score | Details |
|-----------|-------|---------|
| HumanEval (164 problems) | **90.9% pass@1** | GLM-5, $0.26 total |
| Terminal-Bench (10 tasks) | **30%** | GLM-5, frontier models get <65% |
| SWE-bench Lite (20 instances) | **10%** | GLM-5, [transparency report](docs/benchmarks/README.md) |

Full benchmark transparency framework with 13 tracked issues: [docs/benchmarks/README.md](docs/benchmarks/README.md)

## Philosophy

Chimera treats agentic coding as a machine learning problem. The insight (from the observation): when an engineer writes a spec, agents iterate on code, and the result is a codebase that passes all tests -- that's training. The spec is the loss function. The agent loop is the optimizer. The output is a trained model you deploy without inspecting its internals.

The core verb is `.synthesize()` -- program synthesis, not chat.

## Roadmap

- [x] Core framework (8 layers, 20 tools, 6 providers)
- [x] Agent replication (8 architectures: SWE-Agent, Aider, Cline, Codex, OpenHands, Gemini, OpenCode, Kimi)
- [x] Benchmarks (HumanEval 90.9%, Terminal-Bench 30%, SWE-bench 10%)
- [x] Documentation site (Starlight, 114 pages)
- [x] CI/CD pipeline (GitHub Actions)
- [ ] Close SWE-bench gap (IPython action space, LLM condensation, 500 iterations)
- [ ] PyPI publishing (`pip install chimera-ai`)
- [ ] Additional benchmarks (FeatureBench, MBPP, LiveCodeBench, tau-bench)
- [ ] IDE extension

## Contributing

```bash
git clone https://github.com/0bserver07/chimera.git
cd chimera
uv sync --extra dev --extra anthropic
uv run pytest
```

The project uses TDD. Tests go in `tests/`, one file per module. Run `ruff check chimera/` for linting and `mypy chimera/` for type checking.

### Integration tests

Run real provider tests with credentials:

```bash
export ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
export ANTHROPIC_AUTH_TOKEN="your-token"
export ANTHROPIC_MODEL="glm-5"
uv run pytest tests/test_examples.py -v                     # examples against real LLM
uv run pytest tests/test_integration_live.py -v              # provider integration tests
uv run pytest tests/test_provider_anthropic_integration.py -v # full provider tests
```

Tests auto-detect credentials -- they use the real provider when available, mock when not.

## License

AGPL-3.0

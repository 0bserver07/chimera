# Chimera

A composable coding agent framework. Synthesize codebases from specifications.

## Quick Reference

- **Language:** Python 3.11+
- **Build:** hatchling
- **License:** AGPL-3.0
- **Tests:** `python -m pytest` (823 tests)
- **Lint:** `ruff check chimera/`
- **Types:** `mypy chimera/`
- **Docs:** `pip install -e ".[docs]" && mkdocs serve`

## Architecture

6-layer stack (each layer usable independently):

```
Layer 6: CLI           chimera synthesize / chimera eval / chimera bench
Layer 5: Synthesis     Trainer, Strategy, Spec, Architecture, Constraint
Layer 4: Evaluation    Harness, Metrics, Benchmarks
Layer 3: Agent         Agent, Tools, Loops, Prompt, Context
Layer 2: Provider      Anthropic, OpenAI, Google, Ollama, OpenAI-compat
Layer 1: Environment   Local, Docker, Git, persistent shell (tmux)
```

## Module Map

### Core (`chimera/core/`)
- `agent.py` — Agent class, main entry point
- `context.py` — Conversation history manager
- `loop.py` — ReAct loop (reason-act-observe)
- `loop_config.py` — LoopConfig dataclass (permissions, detection, events, etc.)
- `tool_executor.py` — Shared tool execution with permission/event/detection hooks
- `prompt.py` — System prompt with variable substitution
- `tool.py` — BaseTool ABC and @tool decorator
- `tool_group.py` — ToolGroup and DEFAULT_TOOLS
- `loops/` — PlanAndExecute, Reflexion, TreeOfThought

### Providers (`chimera/providers/`)
- `base.py` — Provider ABC, Response, StreamEvent
- `factory.py` — `create_provider()` auto-detection
- `anthropic.py`, `openai_provider.py`, `google.py`, `ollama.py`, `modal.py`
- `cost.py` — Per-model pricing and cost calculation

### Tools (`chimera/tools/`)
13 built-in tools: read, write, edit, bash, search, list_files, test, git, web_fetch, replace_in_file, verify, delegate, repo_map

### Environments (`chimera/env/`)
- `base.py` — Environment ABC
- `local.py` — LocalEnvironment (filesystem)
- `git.py` — GitEnvironment (branch isolation)
- `docker.py` — DockerEnvironment (container isolation)
- `shell.py` — PersistentShell (tmux sessions)

### Training (`chimera/training/`)
- `trainer.py` — Trainer orchestrator
- `spec.py` — Spec (task specification)
- `architecture.py` — Architecture (multi-layer builds)
- `strategies/` — TestConvergence, TreeSearch, Curriculum, Ensemble, MajorityVoting, AIMOEnsemble, Passthrough

### Composition (`chimera/composition/`)
- Pipeline (sequential), Ensemble (parallel), Supervisor (coordinator + workers)

### Evaluation (`chimera/eval/`)
- Harness, Benchmark ABC, metrics (pass@k, resolve_rate, avg_cost)

### Extension Modules (new)
- `chimera/events/` — EventBus, 9 event types, middleware
- `chimera/compaction/` — Token counting, pruning, LLM summarization
- `chimera/detection/` — Loop detection (exact repeat, pattern cycle)
- `chimera/permissions/` — Rule-based permission policies
- `chimera/streaming/` — Stream handlers, StreamingReAct
- `chimera/sessions/` — Multi-turn persistence (memory, file, SQLite)
- `chimera/auth/` — API key, OAuth device/browser flows, credential store
- `chimera/agents/` — AgentConfig, presets (Build, Plan, Explore, General, Review), registry

## Key Conventions

- **Zero-dependency core.** Only stdlib in main package. Providers are optional extras.
- **TYPE_CHECKING imports.** Use `if TYPE_CHECKING:` for cross-module type hints to avoid circular imports.
- **3-tier API.** Every feature has: one-liner convenience, developer configuration, framework-author subclassing.
- **LoopConfig pattern.** All loop-level features (permissions, detection, compaction, streaming, events) funnel through a single `LoopConfig` dataclass injected into loop constructors. When `None`, behavior is unchanged.
- **Google-style docstrings.** Use Args/Returns/Raises sections.
- **Tests mirror source.** `chimera/foo/bar.py` → `tests/test_bar.py` or `tests/test_foo.py`.

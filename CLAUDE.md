# Chimera

A composable coding agent framework. Synthesize codebases from specifications.

## Quick Reference

- **Language:** Python 3.11+
- **Build:** hatchling + uv
- **License:** AGPL-3.0
- **Setup:** `uv sync --extra dev --extra anthropic`
- **Tests:** `uv run pytest` (2046 tests)
- **Lint:** `uv run ruff check chimera/`
- **Types:** `uv run mypy chimera/`
- **Docs:** `uv sync --extra docs && uv run mkdocs serve`

## Architecture

8-layer stack (each layer usable independently):

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

## Module Map

### Core (`chimera/core/`)
- `agent.py` — Agent class, main entry point
- `context.py` — Conversation history manager
- `loop.py` — ReAct loop (reason-act-observe)
- `loop_config.py` — LoopConfig dataclass (permissions, detection, events, audit, checkpoints, git workflow, etc.)
- `tool_executor.py` — Shared tool execution with permission/event/detection/audit hooks
- `prompt.py` — System prompt with variable substitution
- `tool.py` — BaseTool ABC and @tool decorator
- `tool_group.py` — ToolGroup and DEFAULT_TOOLS
- `loops/` — PlanAndExecute, Reflexion, TreeOfThought

### Providers (`chimera/providers/`)
- `base.py` — Provider ABC, Response, StreamEvent
- `factory.py` — `create_provider()` auto-detection
- `anthropic.py`, `openai_provider.py`, `google.py`, `ollama.py`, `modal.py`
- `cost.py` — Per-model pricing and cost calculation
- `cost_tracker.py` — Granular token tracking (cache, reasoning, per-step breakdown)

### Tools (`chimera/tools/`)
20 built-in tools: read, write, edit, bash, search, list_files, test, git, web_fetch, replace_in_file, verify, delegate, repo_map, image_read, browser, import_graph, think, ask_user, todo, dmail

### Environments (`chimera/env/`)
- `base.py` — Environment ABC
- `local.py` — LocalEnvironment (filesystem)
- `git.py` — GitEnvironment (branch isolation)
- `docker.py` — DockerEnvironment (container isolation)
- `remote.py` — RemoteEnvironment (HTTP client to remote server)
- `cloud.py` — CloudEnvironment (managed sandbox provisioning)
- `shell.py` — PersistentShell (tmux sessions)

### Training (`chimera/training/`)
- `trainer.py` — Trainer orchestrator
- `spec.py` — Spec (task specification)
- `architecture.py` — Architecture (multi-layer builds)
- `strategies/` — TestConvergence, TreeSearch, Curriculum, Ensemble, MajorityVoting, AIMOEnsemble, Passthrough, CEGISStrategy, IncrementalStrategy

### Composition (`chimera/composition/`)
- Pipeline (sequential), Ensemble (parallel), Supervisor (coordinator + workers)

### Evaluation (`chimera/eval/`)
- Harness, Benchmark ABC, metrics (pass@k, resolve_rate, avg_cost)
- Benchmarks: SWE-bench, HumanEval, AIMO, Custom

### Workflows (`chimera/workflows/`, `chimera/ci/`, `chimera/review/`, `chimera/research/`, `chimera/migration/`, `chimera/docs/`, `chimera/testgen/`)
- `workflows/git_workflow.py` — GitWorkflow (branch isolation, diff context, commit strategies)
- `ci/fix_workflow.py` — CIFixWorkflow: parse CI logs → prompt → Agent.run() → retry loop
- `review/orchestrator.py` — ReviewOrchestrator: reviewer Agent + author Agent iteration
- `research/researcher.py` — Researcher: plan decomposition → Agent.run() → synthesis
- `migration/planner.py` — MigrationPlanner: rule-based code transforms with presets (python2-to-3, commonjs-to-esm)
- `docs/generator.py` — DocGenerator: AST-based documentation scanning and generation
- `testgen/generator.py` — TestGenerator: source analysis → test case skeletons

### Agent System (`chimera/agents/`)
- `config.py` — AgentConfig with from_markdown(), build(), registries
- `presets/` — Build, Plan, Explore, General, Review preset agents
- `registry.py` — AgentRegistry with register, get, list, load_directory
- `loader.py` — FileAgentDef, AgentLoader (priority: project > user > built-in), AgentFactory

### Critic (`chimera/critic/`)
- `base.py` — Critic ABC, CriticResult, CriticConfig, CriticMode (all_actions / finish_only)
- `llm_critic.py` — LLMCritic (provider-based), ChecklistCritic (rule-based)
- `mixin.py` — CriticMixin for loop integration with iterative refinement

### Agent Client Protocol (`chimera/acp/`)
- `types.py` — ACPSessionConfig, ACPToolCall, ACPResponse
- `client.py` — ACPClient (JSON-RPC 2.0 over subprocess stdio)
- `tool.py` — ExternalAgentTool (wrap external agents as Chimera tools)

### Security (`chimera/security/`)
- `risk.py` — SecurityRisk enum, RiskClassifier
- `analyzer.py` — SecurityAnalyzer ABC, LLMSecurityAnalyzer, RuleBasedSecurityAnalyzer, CompositeSecurityAnalyzer
- `policy.py` — ConfirmationPolicy ABC, NeverConfirm, AlwaysConfirm, ConfirmAboveThreshold

### Secrets (`chimera/secrets/`)
- `registry.py` — SecretRegistry (register, redact, env-var loading)
- `detector.py` — SecretDetector (10 built-in patterns: API keys, AWS, Bearer, private keys, etc.)
- `redactor.py` — RedactionMiddleware for EventBus

### Permissions (`chimera/permissions/`)
- `base.py` — 5 policies (AutoApprove, AlwaysDeny, AllowList, DenyList, custom)
- `audit.py` — AuditEntry, AuditLog (record, summary, for_tool, clear)
- `risk.py` — RiskLevel enum, classify_risk() for bash patterns

### Checkpoints (`chimera/checkpoints.py`)
- CheckpointManager: create, restore_by_name, restore_by_id, undo, list_checkpoints

### Events (`chimera/events/`)
- EventBus, event types (ToolCall, ToolResult, SecurityEvent, CriticEvent, StepCost, ExternalAgent), middleware

### Sessions (`chimera/sessions/`)
- `session.py` — Session with chat(), iter_chat(), fork(), save(), resume()
- `storage/` — Memory, File, SQLite backends
- `eventlog/` — Event-sourced persistence (append-only log, file locking, crash recovery, gap detection)

### Compaction (`chimera/compaction/`)
- `base.py` — CompactionStrategy ABC, AtomicGroup, CompactionView, CompactionUrgency
- `strategies.py` — Summary, Prune, Counter, Composite
- `thresholds.py` — ThresholdCompaction (SOFT/HARD thresholds, tool call/result atomicity)

### Config (`chimera/config/`)
- `union.py` — DiscriminatedUnion base (from_config/to_config dispatch, type field validation)
- `config_file.py` — ChimeraConfig for YAML/JSON loading
- `loader.py` — ProjectConfig discovery

### Plugins (`chimera/plugins/`)
- `base.py` — BasePlugin ABC, ComponentRegistry, Hook, MCPServerConfig
- `manager.py` — PluginManager (load, unload, discover)
- `registry.py` — PluginExtensionRegistry (agents, strategies, constraints, middleware, skills, MCP, hooks)
- `dir_loader.py` — DirectoryPluginLoader (agents/*.md, .mcp.json, hooks/)
- `marketplace.py` — PluginInfo, MarketplaceRegistry, Marketplace (search, install, uninstall)

### Wire (`chimera/wire/`)
- `types.py` — WireMessage, WireRequest/Response, TurnBegin/End, StepBegin/End, ApprovalRequest/Response, UserQuestion/Answer, StatusUpdate
- `wire.py` — Wire bidirectional channel (send, request/response, listeners)

### Skills (`chimera/skills/`)
- `flow.py` — Flow (Mermaid flowchart → decision tree → agent prompt), FlowNode, FlowEdge

### Other Infrastructure
- `chimera/streaming/` — Stream handlers, StreamingReAct
- `chimera/detection/` — Loop detection (exact repeat, pattern cycle)
- `chimera/mcp/` — MCPClient (stdio/HTTP), MCPToolSource, from_config()
- `chimera/lsp/` — LSP client, diagnostics, completion, rename
- `chimera/auth/` — API key, OAuth device/browser flows, credential store

### CLI (`chimera/cli/`)
- `main.py` — 11 subcommands: synthesize, eval, bench, code, review, ci-fix, research, docs, testgen, migrate, plugins
- `code.py` — Interactive REPL with 16 slash commands: /help, /model, /cost, /clear, /history, /tools, /context, /debug, /session, /compact, /audit, /checkpoint, /agent, /init, /yolo, /exit

## Key Conventions

- **Zero-dependency core.** Only stdlib in main package. Providers and tools like browser (playwright), remote env (httpx) are optional extras.
- **TYPE_CHECKING imports.** Use `if TYPE_CHECKING:` for cross-module type hints to avoid circular imports.
- **3-tier API.** Every feature has: one-liner convenience, developer configuration, framework-author subclassing.
- **LoopConfig pattern.** All loop-level features (permissions, detection, compaction, streaming, events, audit, checkpoints, git workflow) funnel through a single `LoopConfig` dataclass injected into loop constructors. When `None`, behavior is unchanged.
- **Google-style docstrings.** Use Args/Returns/Raises sections.
- **Tests mirror source.** `chimera/foo/bar.py` → `tests/test_bar.py` or `tests/test_foo.py`.

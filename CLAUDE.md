# Chimera

Compose coding agents from modular primitives. Synthesize codebases from specifications.

## Quick Reference

- **Language:** Python 3.11+
- **Build:** hatchling + uv
- **License:** MIT
- **Setup:** `uv sync --extra dev --extra anthropic`
- **Tests:** `uv run pytest` (8534 passing + 97 skipped locally, excluding the live-infra files `tests/integration/test_env_docker_integration.py`, `tests/env/test_modal_sandbox.py`, `tests/env/test_ssh_live.py`; one `tests/function_synthesis/test_validation_split.py` test is env-sensitive locally but green in CI)
- **Lint:** `uv run ruff check chimera/`
- **Types:** `uv run mypy chimera/`
- **Docs:** Astro/Starlight in `site/`. Local: `cd site && pnpm install && pnpm dev`. Deploys to <https://0bserver07.github.io/chimera/> via `.github/workflows/ci.yml`.
- **Versioning:** stay in 0.9.x (patch bumps only, batched, unhurried); 1.0 is reserved for a major breakthrough — see `docs/playbooks/14-release-discipline.md`.

## Architecture

8-layer stack (each layer usable independently):

```
Layer 8: CLI             chimera synthesize / eval / bench / code / review /
                         ci-fix / research / docs / testgen / migrate / plugins
Layer 7: Workflows       CIFixWorkflow, ReviewOrchestrator, Researcher,
                         MigrationPlanner, DocGenerator, TestGenerator
Layer 6: Synthesis       Trainer, Strategy, Spec, Architecture, Constraint
Layer 5: Evaluation      Harness, Metrics, Benchmarks (SWE-bench, HumanEval, AIMO)
Layer 4: Agent           Agent, Tools, Loops, Prompt, Context, Critic, ACP,
                         Cancellation, MessageQueues, FileTracker, Operations
Layer 3: Provider        Anthropic, OpenAI, Google, Ollama, Modal, OpenAI-compat,
                         Proxy, Registry, ThinkingLevel
Layer 2: Infrastructure  Security, Secrets, Permissions, Events, Sessions, Wire,
                         Compaction, Streaming, Detection, Config, Plugins, MCP, LSP,
                         SessionTree, RPC
Layer 1: Environment     Local, Docker, Git, Remote, Cloud, PersistentShell
```

## Module Map

### Core (`chimera/core/`)
- `agent.py` — Agent class, main entry point
- `context.py` — Conversation history manager
- `loop.py` — ReAct loop (reason-act-observe), stream-level cancellation, steering drain, 10 lifecycle event emissions
- `loop_config.py` — LoopConfig dataclass (permissions, detection, events, audit, checkpoints, git workflow, cancellation, message_queues, file_tracker)
- `tool_executor.py` — Shared tool execution with permission/event/detection/audit/cancellation/file-tracking hooks
- `prompt.py` — System prompt with variable substitution
- `tool.py` — BaseTool ABC and @tool decorator
- `tool_group.py` — ToolGroup, DEFAULT_TOOLS, `create_default_tools(ops=...)` factory
- `cancellation.py` — CancellationToken (thread-safe cooperative cancel), OperationCancelled, CancellableTool mixin
- `file_tracker.py` — FileTracker (track files read/modified across compaction boundaries)
- `message_queue.py` — MessageQueues (thread-safe steering + follow-up queues)
- `operations.py` — ReadOps, WriteOps, BashOps, SearchOps protocols + Local implementations
- `loops/` — PlanAndExecute, Reflexion, TreeOfThought

### Providers (`chimera/providers/`)
- `base.py` — Provider ABC (with `thinking` param), Response, StreamEvent
- `factory.py` — `create_provider()` auto-detection via registry
- `registry.py` — Runtime provider registry (`register_provider`, `get_provider_factory`, self-registration)
- `thinking.py` — ThinkingLevel enum (OFF/MINIMAL/LOW/MEDIUM/HIGH/MAX), `budget_for_level()`
- `proxy.py` — ProxyProvider (HTTP relay for centralized key management)
- `anthropic.py`, `openai_provider.py`, `google.py`, `ollama.py`, `modal.py`, `compatible.py`
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
- EventBus, 26 event types, middleware
- Core: ToolCall, ToolResult, Step, TextDelta, Error, LoopDetected, Permission, Session, StepCost
- Lifecycle: AgentStart/End, TurnStart/End, StreamStart/End, ModelRequest/Response
- Advanced: Compaction, Critic, ExternalAgent (Start/Complete/ToolCall), Security, Steering, Cancellation

### Sessions (`chimera/sessions/`)
- `session.py` — Session with chat(), iter_chat(), fork(), save(), resume(), steer(), queue(), cancel(), auto-compaction
- `tree.py` — SessionTree (JSONL persistence with in-place branching via parent_id, fork, switch, thread-safe)
- `storage/` — Memory, File, SQLite backends
- `eventlog/` — Event-sourced persistence (append-only log, file locking, crash recovery, gap detection)

### Compaction (`chimera/compaction/`)
- `base.py` — CompactionStrategy ABC, AtomicGroup, CompactionView, CompactionUrgency, CompactionMetadata, FileAwareCompaction
- `summary.py` — SummaryCompaction (extends FileAwareCompaction, includes file tracking in summaries)
- `strategies.py` — Prune, Counter, Composite
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
- `discovery.py` — Skill discovery (walk SKILL.md files with YAML frontmatter), `discover_skills()`, `format_skills_for_prompt()`

### Other Infrastructure
- `chimera/streaming/` — Stream handlers, StreamingReAct
- `chimera/detection/` — Loop detection (exact repeat, pattern cycle)
- `chimera/mcp/` — MCPClient (stdio/HTTP), MCPToolSource, from_config()
- `chimera/mcp_servers/` — 7 stdin/stdout JSON-RPC servers (search, review, testgen, migration, rag, benchmark, team coordination) + teammate_runner (drives external agent CLIs against a team queue)
- `chimera/lsp/` — LSP client, diagnostics, completion, rename
- `chimera/auth/` — API key, OAuth device/browser flows (real stdlib HTTP impl), credential store (file-based, 0o600 perms)
- `chimera/rpc/` — JSON-RPC server (stdin/stdout), RpcHandler (prompt/steer/cancel/get_state/compact), command/response/event types

### Assembly (`chimera/assembly/`)
- `coding_agent.py` — CodingAgent, the assembled daily-driver stack behind `chimera code` (presets, conversation memory, loop postures via `LOOP_POSTURES`)
- `driver.py` — AgentDriver: the one control surface a REPL/TUI drives (send/steer/cancel/clear/load_history + model/tools/cost/history)
- `loop_adapter.py` — run a strategy loop (plan-execute/reflexion/tot) as a LoopEvent stream (worker-thread bridge, bounded provider)
- `presets.py` / `system_prompts.py` / `tool_sets.py` — AssemblyConfig PRESETS (coding_agent, codex, minimal, explore), prompts, tool factories

### TUI (`chimera/tui/`)
Interactive frontends over AgentDriver (spec: `docs/specs/interactive-frontends.md`, all 3 phases shipped):
- `app.py` — single-agent full-screen TUI (`chimera code --tui`)
- `multiplex.py` — the multiplexer: N lanes race one task (`--tui --models a[:preset[:loop]],b,…` / `chimera otter --multiplex`), broadcast/targeted routing, resume
- `lane.py` / `cohort.py` — Lane (driver+workspace+telemetry+tool_log), Cohort (manifest, persistence to `~/.chimera/cohorts/`, export, list/load for `--resume`)
- `workspace.py` — per-lane isolation (git worktree per lane, copy fallback; `apply_diff` for resume)
- `render.py` / `results.py` / `prompt.py` / `routing.py` / `history_io.py` — shared transcript rendering (markdown assistant prose, collapsed reasoning), comparison screen (scoreboard + per-file/split diffs), multi-line prompt + slash autocomplete, pure input routing, faithful history codec

### Codename Agent CLIs (`chimera/{mink,otter,ferret,weasel,shrew,stoat,badger}/`)
- 7 replicated coding-agent CLIs: `chimera mink|otter|ferret|weasel|shrew|stoat|badger` (aliases: tui, multi, sandbox, mini, tiny, shell, strict)
- `mink/team.py` — `chimera team` subcommand (create/join/task/status/ls/rm/watch/approvals/roles)
- `mink/team_approvals.py` — interactive plan-approval loop for team leads
- `cli/agent_teams.py` — Team + TeamMailbox primitives (file-locked JSONL task queue, deps, requires_plan gate)

### CLI (`chimera/cli/`)
- `main.py` — 30+ subcommands: synthesize, eval, bench, code, the 7 codename CLIs, team, resume, agents, review, ci-fix, research, docs, testgen, migrate, fs, config, which, tier-status, completion, plugins, doctor, auth
- `code.py` — Interactive REPL with 19 slash commands, two-mode terminal (readline idle / raw stdin running), threaded agent execution
  - Commands: /help, /model (next/prev cycling), /cost, /clear, /history, /tools, /context, /debug, /session, /compact, /audit, /checkpoint, /agent, /init, /yolo, /tree, /branch, /switch, /exit
  - Flags: `--mode interactive|rpc|json`, `--models glm-5,claude-sonnet-4` (comma-separated for cycling)
  - Features: mid-turn steering, Ctrl+C cancellation, session auto-save to `~/.chimera/sessions/`, auto-compaction, skills discovery

## Key Conventions

- **Zero-dependency core.** Only stdlib in main package. Providers and tools like browser (playwright), remote env (httpx) are optional extras.
- **TYPE_CHECKING imports.** Use `if TYPE_CHECKING:` for cross-module type hints to avoid circular imports.
- **3-tier API.** Every feature has: one-liner convenience, developer configuration, framework-author subclassing.
- **LoopConfig pattern.** All loop-level features (permissions, detection, compaction, streaming, events, audit, checkpoints, git workflow, cancellation, message queues, file tracking) funnel through a single `LoopConfig` dataclass injected into loop constructors. When `None`, behavior is unchanged.
- **Google-style docstrings.** Use Args/Returns/Raises sections.
- **Tests mirror source.** `chimera/foo/bar.py` → `tests/test_bar.py` or `tests/test_foo.py`.

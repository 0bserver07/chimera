# Chimera — Task Status

> 256 tasks across 39 phases. TDD approach: tests first, then implementation.
> Sources: `docs/plans/2026-02-20-chimera-implementation-plan.md`, `docs/plans/2026-02-20-chimera-extension-plan.md`, `docs/plans/2026-02-22-persistent-shell-plan.md`, `docs/plans/2026-02-25-aimo3-implementation-plan.md`

## Phases 1–8: Core Framework (Original)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 1 | 1 - Scaffold | Project setup | `pyproject.toml`, `chimera/__init__.py`, `chimera/py.typed`, `tests/__init__.py` | — | DONE |
| 2 | 1 - Scaffold | Core data types | `chimera/types.py` | 11 | DONE |
| 3 | 2 - Environment | Environment protocol | `chimera/env/base.py`, `chimera/env/__init__.py` | 1 | DONE |
| 4 | 2 - Environment | LocalEnvironment | `chimera/env/local.py` | 8 | DONE |
| 5 | 3 - Provider | Provider protocol | `chimera/providers/base.py`, `chimera/providers/__init__.py` | 4 | DONE |
| 6 | 3 - Provider | AnthropicProvider | `chimera/providers/anthropic.py` | 5 | DONE |
| 7 | 4 - Tools | Tool protocol | `chimera/core/tool.py`, `chimera/core/__init__.py` | 5 | DONE |
| 8 | 4 - Tools | Built-in tools | `chimera/tools/read.py`, `write.py`, `bash.py`, `__init__.py` | 9 | DONE |
| 9 | 5 - Agent | Context and Prompt | `chimera/core/context.py`, `chimera/core/prompt.py` | 13 | DONE |
| 10 | 5 - Agent | ReAct loop | `chimera/core/loop.py` | 8 | DONE |
| 11 | 5 - Agent | Agent class | `chimera/core/agent.py` | 7 | DONE |
| 12 | 6 - Synthesis | Spec and Architecture | `chimera/training/spec.py`, `architecture.py`, `__init__.py` | 21 | DONE |
| 13 | 6 - Synthesis | Constraints | `chimera/training/constraint.py` | 30 | DONE |
| 14 | 6 - Synthesis | TestConvergence strategy | `chimera/training/strategies/base.py`, `convergence.py`, `__init__.py` | 16 | DONE |
| 15 | 6 - Synthesis | Trainer and Callbacks | `chimera/training/trainer.py`, `callbacks.py` | 14 | DONE |
| 16 | 7 - Integration | Public API | `chimera/__init__.py` | — | DONE |
| 17 | 7 - Integration | Integration test | `tests/test_integration.py` | 3 | DONE |
| 18 | 8 - CLI | `chimera synthesize` command | `chimera/cli/main.py`, `chimera/cli/__init__.py` | 8 | DONE |

## Phases 9–13: Extension (Tools, Providers, Composition, Eval, Polish)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 19 | 9 - Tools | EditFileTool | `chimera/tools/edit.py` | 5 | DONE |
| 20 | 9 - Tools | SearchTool | `chimera/tools/search.py` | 5 | DONE |
| 21 | 9 - Tools | ListFilesTool | `chimera/tools/list_files.py` | 5 | DONE |
| 22 | 9 - Tools | TestTool | `chimera/tools/test.py` | 4 | DONE |
| 23 | 9 - Tools | WebFetchTool | `chimera/tools/web_fetch.py` | 4 | DONE |
| 24 | 9 - Tools | GitTool | `chimera/tools/git.py` | 6 | DONE |
| 25 | 9 - Tools | ReplaceInFileTool | `chimera/tools/replace_in_file.py` | 5 | DONE |
| 26 | 9 - Internals | Approval policies | `chimera/core/approval.py` | 7 | DONE |
| 27 | 9 - Internals | ToolGroup | `chimera/core/tool_group.py` | 6 | DONE |
| 28 | 9 - Internals | DelegateTool | `chimera/tools/delegate.py` | 3 | DONE |
| 29 | 9 - Internals | Loop detection | `chimera/core/loop_detection.py` | 6 | DONE |
| 30 | 9 - Internals | Context compression | `chimera/core/compression.py` | 5 | DONE |
| 31 | 9 - Internals | Streaming | `chimera/core/streaming.py` | 4 | DONE |
| 32 | 10 - Providers | OpenAIProvider | `chimera/providers/openai.py` | 5 | DONE |
| 33 | 10 - Providers | GoogleProvider | `chimera/providers/google.py` | 4 | DONE |
| 34 | 10 - Providers | OllamaProvider | `chimera/providers/ollama.py` | 4 | DONE |
| 35 | 10 - Providers | OpenAICompatibleProvider | `chimera/providers/compatible.py` | 4 | DONE |
| 36 | 10 - Providers | Provider factory | `chimera/providers/factory.py` | 6 | DONE |
| 37 | 11 - Composition | Pipeline | `chimera/composition/pipeline.py` | 3 | DONE |
| 38 | 11 - Composition | Ensemble | `chimera/composition/ensemble.py` | 3 | DONE |
| 39 | 11 - Composition | Supervisor | `chimera/composition/supervisor.py` | 2 | DONE |
| 40 | 11 - Loops | PlanAndExecute | `chimera/core/loops/plan_execute.py` | 2 | DONE |
| 41 | 11 - Loops | Reflexion | `chimera/core/loops/reflexion.py` | 3 | DONE |
| 42 | 11 - Loops | TreeOfThought | `chimera/core/loops/tree_of_thought.py` | 3 | DONE |
| 43 | 11 - Strategies | CurriculumStrategy | `chimera/training/strategies/curriculum.py` | 4 | DONE |
| 44 | 11 - Strategies | EnsembleStrategy | `chimera/training/strategies/ensemble.py` | 3 | DONE |
| 45 | 11 - Strategies | Passthrough | `chimera/training/strategies/passthrough.py` | 4 | DONE |
| 46 | 12 - Eval | Harness + Benchmark | `chimera/eval/harness.py` | 6 | DONE |
| 47 | 12 - Eval | Metrics | `chimera/eval/metrics.py` | 14 | DONE |
| 48 | 12 - Eval | AntiOverfit | `chimera/eval/anti_overfit.py` | 9 | DONE |
| 49 | 12 - Benchmarks | SWE-bench adapter | `chimera/eval/benchmarks/swe_bench.py` | 9 | DONE |
| 50 | 12 - Benchmarks | HumanEval adapter | `chimera/eval/benchmarks/human_eval.py` | 8 | DONE |
| 51 | 12 - Benchmarks | Custom benchmark | `chimera/eval/benchmarks/custom.py` | 8 | DONE |
| 52 | 13 - Environments | DockerEnvironment | `chimera/env/docker.py` | 9 | DONE |
| 53 | 13 - Environments | GitEnvironment | `chimera/env/git_env.py` | 6 | DONE |
| 54 | 13 - CLI | `chimera eval` command | `chimera/cli/main.py` | 3 | DONE |
| 55 | 13 - CLI | `chimera bench` command | `chimera/cli/main.py` | 3 | DONE |
| 56 | 13 - Constraints | Extended constraints | `chimera/training/constraint.py` | 11 | DONE |
| 57 | 13 - Callbacks | ProgressBar | `chimera/training/callbacks.py` | 4 | DONE |

## Phase 14: Persistent Shell

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 58 | 14 - Persistent Shell | SessionMixin core | `chimera/env/session.py` | 5 | DONE |
| 59 | 14 - Persistent Shell | Named shells | `chimera/env/session.py` | 4 | DONE |
| 60 | 14 - Persistent Shell | run_in_session | `chimera/env/session.py` | 8 | DONE |
| 61 | 14 - Persistent Shell | Environment ABC update | `chimera/env/base.py` | 1 | DONE |
| 62 | 14 - Persistent Shell | LocalEnvironment integration | `chimera/env/local.py` | 8 | DONE |
| 63 | 14 - Persistent Shell | GitEnvironment inheritance | `tests/test_env_git.py` | 1 | DONE |
| 64 | 14 - Persistent Shell | Package exports | `chimera/env/__init__.py`, `chimera/__init__.py` | 2 | DONE |

## Phase 15: Cost Tracking

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 65 | 15 - Cost | Cost calculation utility | `chimera/providers/cost.py` | 9 | DONE |
| 66 | 15 - Cost | ReAct loop cost aggregation | `chimera/core/loop.py` | 1 | DONE |
| 67 | 15 - Cost | Other loops cost aggregation | `chimera/core/loops/*.py` | 3 | DONE |
| 68 | 15 - Cost | Cost integration test | `tests/test_integration.py` | 1 | DONE |

## Phase 16: synthesize() One-Liner + CLI

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 69 | 16 - Synthesize | chimera.synthesize() function | `chimera/synthesize.py` | 3 | DONE |
| 70 | 16 - Synthesize | Export synthesize | `chimera/__init__.py` | 1 | DONE |
| 71 | 16 - Synthesize | Wire CLI run_synthesize() | `chimera/cli/main.py` | 2 | DONE |

## Phase 17: Repository Mapping

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 72 | 17 - RepoMap | RepoMap core class | `chimera/tools/repo_map.py` | 7 | DONE |
| 73 | 17 - RepoMap | RepoMapTool integration | `tests/test_repo_map.py` | 4 | DONE |
| 74 | 17 - RepoMap | Package exports | `chimera/__init__.py` | 2 | DONE |

## Phase 18: Tree Search Strategy

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 75 | 18 - Tree Search | SearchNode data model | `chimera/training/strategies/tree_search.py` | 3 | DONE |
| 76 | 18 - Tree Search | TreeSearch constructor | `chimera/training/strategies/tree_search.py` | 3 | DONE |
| 77 | 18 - Tree Search | Environment cloning | `chimera/training/strategies/tree_search.py` | 3 | DONE |
| 78 | 18 - Tree Search | Core search loop | `chimera/training/strategies/tree_search.py` | 6 | DONE |
| 79 | 18 - Tree Search | Custom branch_fn | `tests/test_strategy_tree_search.py` | 1 | DONE |
| 80 | 18 - Tree Search | Package exports | `chimera/__init__.py` | 3 | DONE |

## Phase 19: AIMO3 Competition

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 81 | 19 - AIMO3 | ModalProvider | `chimera/providers/modal.py` | 6 | DONE |
| 82 | 19 - AIMO3 | VerifyTool | `chimera/tools/verify.py` | 8 | DONE |
| 83 | 19 - AIMO3 | MajorityVoting strategy | `chimera/training/strategies/majority_voting.py` | 8 | DONE |
| 84 | 19 - AIMO3 | AIMOBenchmark | `chimera/eval/benchmarks/aimo.py` | 14 | DONE |
| 85 | 19 - AIMO3 | AIMOEnsemble strategy | `chimera/training/strategies/aimo_ensemble.py` | 3 | DONE |
| 86 | 19 - AIMO3 | AIMO3 integration test | `tests/test_aimo_integration.py` | 3 | DONE |
| 87 | 19 - AIMO3 | Kaggle notebook template | `chimera/notebooks/aimo3/` | — | DONE |
| 88 | 19 - AIMO3 | Package exports | `chimera/__init__.py` | — | DONE |

## Phase 20: Provider Integration + Docs

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 89 | 20 - Integration | `ANTHROPIC_AUTH_TOKEN` support | `chimera/providers/anthropic.py` | — | DONE |
| 90 | 20 - Integration | Env-based provider inference | `chimera/providers/factory.py` | — | DONE |
| 91 | 20 - Integration | Anthropic provider integration tests | `tests/test_provider_anthropic_integration.py` | 12 | DONE |
| 92 | 20 - Docs | Getting started guide | `docs/getting-started.md` | — | DONE |
| 93 | 20 - Docs | Runnable examples | `examples/quickstart_provider.py`, `quickstart_synthesize.py` | — | DONE |
| 94 | 20 - Docs | README update (providers, env vars) | `README.md` | — | DONE |

## Phase 21: Battle-Testing (Bugfixes + GLM-5 Integration Tests)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 95 | 21 - Bugfix | TreeSearch: fix infinite loop on all-branches-fail | `chimera/training/strategies/tree_search.py` | 1 | DONE |
| 96 | 21 - Bugfix | TreeSearch: add logging for failed branches | `chimera/training/strategies/tree_search.py` | — | DONE |
| 97 | 21 - Bugfix | TreeSearch: fix premature convergence with 0 tests | `chimera/training/strategies/tree_search.py` | — | DONE |
| 98 | 21 - Bugfix | MajorityVoting: decouple from AIMO (`extract_fn`) | `chimera/training/strategies/majority_voting.py` | — | DONE |
| 99 | 21 - Bugfix | Ensemble: fix misleading "parallel" docstring | `chimera/composition/ensemble.py` | — | DONE |
| 100 | 21 - GLM-5 | TreeSearch integration test | `tests/test_tree_search_integration.py` | 2 | DONE |
| 101 | 21 - GLM-5 | MajorityVoting integration test | `tests/test_majority_voting_integration.py` | 3 | DONE |
| 102 | 21 - GLM-5 | Eval Harness integration test | `tests/test_eval_harness_integration.py` | 3 | DONE |

## Phase 22: API Gaps (Streaming, Async, Step Iteration)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 103 | 22 - Streaming | Provider streaming (Anthropic, OpenAI, default) | `chimera/providers/anthropic.py`, `openai_provider.py` | 29 | DONE |
| 104 | 22 - Async | Async provider + async loops | `chimera/providers/base.py`, `chimera/core/loops/` | 9 | DONE |
| 105 | 22 - Step Iter | iter_steps + iter_steps for loops | `chimera/core/loop.py`, `chimera/core/loops/` | 21 | DONE |
| 106 | 22 - Tools | Incremental tool executor | `chimera/core/tool_executor.py` | 10 | DONE |
| 107 | 22 - Types | FileChange, extended types, tool result metadata | `chimera/types.py` | 21 | DONE |
| 108 | 22 - LSP | LSP client (initial) | `chimera/lsp/client.py` | 10 | DONE |
| 109 | 22 - Infra | Agents module, auth, compaction, detection, events, loop config, permissions, sessions | multiple | 311 | DONE |

## Phase 23: Infrastructure Hardening

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 110 | 23 - Pricing | Custom model pricing | `chimera/providers/cost.py` | 4 | DONE |
| 111 | 23 - Ensemble | Parallel ensemble (ThreadPoolExecutor) | `chimera/composition/ensemble.py` | 14 | DONE |
| 112 | 23 - Parsers | Multi-language parsers | `chimera/tools/parsers/` | 28 | DONE |
| 113 | 23 - Plugins | Plugin system | `chimera/plugins/` | 10 | DONE |
| 114 | 23 - Transactions | Multi-file edit transactions | `chimera/core/transactions.py` | 14 | DONE |
| 115 | 23 - Docker | Docker integration tests | `tests/test_env_docker_integration.py` | 9 | DONE |

## Phase 24: Agent Intelligence

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 116 | 24 - Edits | Fuzzy edit strategies | `chimera/tools/edit_strategies.py` | 15 | DONE |
| 117 | 24 - Catalog | Provider catalog | `chimera/providers/catalog.py` | 14 | DONE |
| 118 | 24 - Config | Project config (YAML/JSON loading) | `chimera/config/` | 22 | DONE |
| 119 | 24 - MCP | MCP client rewrite (stdio + HTTP, retry, ping) | `chimera/mcp/` | 28 | DONE |
| 120 | 24 - LSP | LSP rewrite (completion, rename, code_action, background) | `chimera/lsp/` | 27 | DONE |

## Phase 25: Serving Layer

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 121 | 25 - Streaming | StreamingReAct (merge into loop) | `chimera/streaming/` | 7 | DONE |
| 122 | 25 - CLI | `chimera eval` + `chimera bench` CLI wiring | `chimera/cli/main.py` | 8 | DONE |
| 123 | 25 - REPL | `chimera code` REPL with streaming | `chimera/cli/code.py` | 7 | DONE |

## Phase 26: Production Polish (Async + MCP + LSP + CostTracker + REPL)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 124 | 26 - Async | BaseTool.async_execute + incremental | `chimera/core/tool.py`, `tool_executor.py` | 8 | DONE |
| 125 | 26 - Async | ReAct.async_iter_steps + async_drain_steps | `chimera/core/loop.py` | 6 | DONE |
| 126 | 26 - Async | Ensemble.async_run + first_success | `chimera/composition/ensemble.py` | 6 | DONE |
| 127 | 26 - MCP | MCP retry with backoff + ping + refresh_tools | `chimera/mcp/client.py` | 12 | DONE |
| 128 | 26 - LSP | LSP background reader + completion + rename + code_action | `chimera/lsp/client.py` | 19 | DONE |
| 129 | 26 - Cost | CostTracker with budgets + estimate_cost + LoopConfig | `chimera/providers/cost_tracker.py` | 10 | DONE |
| 130 | 26 - REPL | Readline integration + 12 slash commands | `chimera/cli/code.py` | 12 | DONE |

## Phase 27: Production Features (15 Features)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 131 | 27 - Git | GitWorkflow (branch isolation, diffs, commits) | `chimera/workflows/git_workflow.py` | 11 | DONE |
| 132 | 27 - Image | Image support (read, display) | `chimera/tools/image_read.py` | 11 | DONE |
| 133 | 27 - Permissions | Permission UX (interactive, policy presets) | `chimera/permissions/` | 20 | DONE |
| 134 | 27 - Graph | Import graph analysis | `chimera/tools/import_graph.py` | 12 | DONE |
| 135 | 27 - Checkpoints | CheckpointManager (create, restore, undo) | `chimera/checkpoints.py` | 14 | DONE |
| 136 | 27 - Agents | AgentLoader (file-based agent definitions) | `chimera/agents/loader.py` | 9 | DONE |
| 137 | 27 - MCP | MCP from_config() | `chimera/mcp/config.py` | 4 | DONE |
| 138 | 27 - SWE | SWE-bench runner | `chimera/eval/benchmarks/swe_bench.py` | 11 | DONE |
| 139 | 27 - CI | CIFixWorkflow | `chimera/ci/fix_workflow.py` | 16 | DONE |
| 140 | 27 - Review | ReviewOrchestrator | `chimera/review/orchestrator.py` | 18 | DONE |
| 141 | 27 - Docs | DocGenerator | `chimera/docs/generator.py` | 8 | DONE |
| 142 | 27 - TestGen | TestGenerator | `chimera/testgen/generator.py` | 15 | DONE |
| 143 | 27 - Migration | MigrationPlanner | `chimera/migration/planner.py` | 14 | DONE |
| 144 | 27 - Research | Researcher | `chimera/research/researcher.py` | 15 | DONE |
| 145 | 27 - Marketplace | Plugin marketplace | `chimera/plugins/marketplace.py` | 14 | DONE |
| 146 | 27 - CLI | 6 new CLI subcommands (review, ci-fix, research, docs, testgen, migrate) | `chimera/cli/main.py` | 22 | DONE |
| 147 | 27 - LoopConfig | LoopConfig hooks (events, audit, checkpoints, git) | `chimera/core/loop_config.py` | 6 | DONE |

## Phase 28: OpenHands-Inspired Features (12 Modules)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 148 | 28 - Security | SecurityAnalyzer (LLM, rule-based, composite) | `chimera/security/` | 32 | DONE |
| 149 | 28 - Secrets | SecretRegistry + SecretDetector + RedactionMiddleware | `chimera/secrets/` | 30 | DONE |
| 150 | 28 - ACP | Agent Client Protocol (JSON-RPC 2.0 subprocess) | `chimera/acp/` | 19 | DONE |
| 151 | 28 - Critic | Critic ABC + LLMCritic + ChecklistCritic | `chimera/critic/` | 22 | DONE |
| 152 | 28 - Compaction | CompactionStrategy + ThresholdCompaction + atomicity | `chimera/compaction/` | 21 | DONE |
| 153 | 28 - EventLog | Event-sourced persistence (append-only, crash recovery) | `chimera/sessions/eventlog/` | 27 | DONE |
| 154 | 28 - CostTracker | Granular token tracking (cache, reasoning, per-step) | `chimera/providers/cost_tracker.py` | 38 | DONE |
| 155 | 28 - Config | DiscriminatedUnion (from_config/to_config dispatch) | `chimera/config/union.py` | 16 | DONE |
| 156 | 28 - Agents | FileAgentDef + AgentRegistry + loader priority | `chimera/agents/` | 17 | DONE |
| 157 | 28 - Plugins | PluginExtensionRegistry (agents, strategies, middleware, hooks, MCP) | `chimera/plugins/` | 21 | DONE |
| 158 | 28 - Env | CloudEnvironment + RemoteEnvironment | `chimera/env/cloud.py`, `remote.py` | 29 | DONE |
| 159 | 28 - Browser | BrowserTool (Playwright-based) | `chimera/tools/browser.py` | 34 | DONE |

## Phase 29: Documentation Overhaul

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 160 | 29 - Docs | CLAUDE.md — full 8-layer architecture | `CLAUDE.md` | — | DONE |
| 161 | 29 - Docs | README + landing page + modules index | `README.md`, `docs/` | — | DONE |
| 162 | 29 - Docs | Architecture page (8-layer stack) | `docs/architecture.md` | — | DONE |
| 163 | 29 - Docs | Module + workflow + reference pages | `docs/modules/`, `docs/reference/` | — | DONE |
| 164 | 29 - Docs | 5 practical how-to guides | `docs/guides/` | — | DONE |
| 165 | 29 - Docs | MkDocs nav update | `mkdocs.yml` | — | DONE |

## Phase 30: Kimi CLI Features

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 166 | 30 - Wire | Wire protocol (bidirectional channel) | `chimera/wire/` | 13 | DONE |
| 167 | 30 - Todo | TodoTool (task tracking in-loop) | `chimera/tools/todo.py` | 11 | DONE |
| 168 | 30 - DMail | DMailTool (context rewind) | `chimera/tools/dmail.py` | 15 | DONE |
| 169 | 30 - Flow | Flow skills (Mermaid → decision tree → prompt) | `chimera/skills/flow.py` | 15 | DONE |
| 170 | 30 - Think | ThinkTool + AskUserTool | `chimera/tools/think.py`, `ask_user.py` | 9 | DONE |
| 171 | 30 - Integration | Kimi e2e integration + wire integration | `tests/test_kimi_e2e.py`, `test_wire_integration.py` | 12 | DONE |
| 172 | 30 - Examples | 8 runnable examples + example tests | `examples/`, `tests/test_examples.py` | 14 | DONE |

## Phase 31: ML & Program Synthesis Primitives (14 Modules)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 173 | 31 - ML | Training curves (epoch logging + diagnostics) | `chimera/training/curves.py` | 13 | DONE |
| 174 | 31 - ML | Validation split (test file splitting) | `chimera/training/validation_split.py` | 9 | DONE |
| 175 | 31 - ML | Regularization (score computation) | `chimera/training/regularization.py` | 6 | DONE |
| 176 | 31 - ML | Tuner (grid search) | `chimera/training/tuner.py` | 8 | DONE |
| 177 | 31 - ML | Sketch (partial program templates) | `chimera/training/sketch.py` | 6 | DONE |
| 178 | 31 - ML | Oracle (test generation triggers) | `chimera/training/oracle.py` | 7 | DONE |
| 179 | 31 - Synthesis | CEGIS strategy (counterexample-guided) | `chimera/training/strategies/cegis.py` | 5 | DONE |
| 180 | 31 - Synthesis | Incremental strategy (function targeting) | `chimera/training/strategies/incremental.py` | 9 | DONE |
| 181 | 31 - Synthesis | Fault localization (suspiciousness ranking) | `chimera/training/fault_localization.py` | 5 | DONE |
| 182 | 31 - Synthesis | Impact analysis (caller/importer finding) | `chimera/training/impact.py` | 6 | DONE |
| 183 | 31 - Synthesis | Mutation testing (mutant generation) | `chimera/training/mutation.py` | 5 | DONE |
| 184 | 31 - Synthesis | Spec inference (invariant detection) | `chimera/training/spec_inference.py` | 6 | DONE |
| 185 | 31 - Constraints | Static analysis constraints (type-check, lint) | `chimera/training/constraint.py` | 8 | DONE |

## Phase 32: Real LLM Verification

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 186 | 32 - Verify | 4 synthesis verification examples (GLM-5) | `examples/synthesis_*.py`, `examples/validation_split.py` | — | DONE |
| 187 | 32 - Verify | Full feature verification with real LLM | `examples/workflow_verification.py` | — | DONE |
| 188 | 32 - Verify | 11 live integration tests | `tests/test_integration_live.py` | 11 | DONE |
| 189 | 32 - Docs | Module integration checklist | `docs/guides/module-integration-checklist.md` | — | DONE |

## Phase 33: Coding Agent Primitives (Waves 1–5)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 190 | 33 - Wave 1 | FocusChain (token budgeting) | `chimera/context/focus_chain.py` | 6 | DONE |
| 191 | 33 - Wave 1 | HistoryProcessor (truncate/prune/compress) | `chimera/context/history_processor.py` | 6 | DONE |
| 192 | 33 - Wave 1 | ContextMention (@file/@folder parsing) | `chimera/context/mentions.py` | 12 | DONE |
| 193 | 33 - Wave 2 | RetryLoop (retry with backoff) | `chimera/core/loops/retry.py` | 11 | DONE |
| 194 | 33 - Wave 2 | PlanActLoop (plan → act phases) | `chimera/core/loops/plan_act.py` | 15 | DONE |
| 195 | 33 - Wave 2 | LintFeedbackLoop (ruff integration) | `chimera/core/loops/lint_feedback.py` | 10 | DONE |
| 196 | 33 - Wave 3 | TreeSitterParser (graceful fallback) | `chimera/tools/parsers/tree_sitter.py` | 13 | DONE |
| 197 | 33 - Wave 3 | DefinitionLookup (AST symbol finding) | `chimera/tools/parsers/definition_lookup.py` | 26 | DONE |
| 198 | 33 - Wave 3 | DemonstrationPrompt (few-shot formatting) | `chimera/core/demonstration_prompt.py` | 17 | DONE |
| 199 | 33 - Wave 4 | SandboxPolicy (path/network/command checking) | `chimera/security/sandbox.py` | 11 | DONE |
| 200 | 33 - Wave 4 | LongTermMemory (JSON persistence) | `chimera/sessions/long_term_memory.py` | 16 | DONE |
| 201 | 33 - Wave 4 | InstructionLayer (layer composition) | `chimera/core/instruction_layer.py` | 13 | DONE |
| 202 | 33 - Wave 5 | AgentPreset (SWE_AGENT, AIDER, CLINE, CODEX) | `chimera/agents/presets/agent_styles.py` | 12 | DONE |

## Phase 34: Production Agent Infrastructure

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 203 | 34 - Middleware | Middleware system (before/after hooks) | `chimera/core/middleware.py` | 13 | DONE |
| 204 | 34 - Queue | MessageQueue (thread-safe injection) | `chimera/core/message_queue.py` | 10 | DONE |
| 205 | 34 - Server | AgentServer (webhook HTTP server) | `chimera/server/webhook.py` | 11 | DONE |
| 206 | 34 - E2E | Preset e2e tests (4 presets verified with real GLM-5) | `tests/test_presets_e2e.py` | 9 | DONE |

## Phase 35: Layer Integration (Prove the Stack Works)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 207 | 35 - Integration | Agent + EventBus + CostTracker + Middleware + Env | `tests/test_layer_integration.py` | 1 | DONE |
| 208 | 35 - Integration | Pipeline + EventBus (two-agent composition) | `tests/test_layer_integration.py` | 1 | DONE |
| 209 | 35 - Integration | synthesize() end-to-end (Trainer + TestConvergence) | `tests/test_layer_integration.py` | 1 | DONE |
| 210 | 35 - Integration | AgentPreset + Wire protocol monitoring | `tests/test_layer_integration.py` | 1 | DONE |
| 211 | 35 - Integration | Ensemble + shared CostTracker + EventBus | `tests/test_layer_integration.py` | 1 | DONE |
| 212 | 35 - Integration | Full vertical slice (6 layers: Env → Secrets → Events → Agent → Synthesis) | `tests/test_layer_integration.py` | 1 | DONE |

## Phase 36: Coding Agent Replication (Close All Gaps)

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 213 | 36 - Search | WebSearchTool (DuckDuckGo, no API key) | `chimera/tools/web_search.py` | 10 | DONE |
| 214 | 36 - Provider | Prompt caching (cache_control on system/tools) | `chimera/providers/anthropic.py` | 6 | DONE |
| 215 | 36 - Provider | Extended thinking (thinking budget, temp=1) | `chimera/providers/anthropic.py` | 5 | DONE |
| 216 | 36 - Sandbox | Docker sandbox enforcement (network, memory, pids, read-only) | `chimera/env/docker.py` | 5 | DONE |
| 217 | 36 - Permissions | Interactive approval UX (y/n/always, memory) | `chimera/permissions/interactive.py` | 5 | DONE |
| 218 | 36 - Config | Project config auto-discovery (CHIMERA.md/CLAUDE.md) | `chimera/config/project_discovery.py` | 6 | DONE |
| 219 | 36 - Agents | Microagent spawning (scoped, budget-limited sub-agents) | `chimera/agents/microagent.py` | 3 | DONE |
| 220 | 36 - Controller | AgentController FSM (7 states, hooks, serialization) | `chimera/core/controller.py` | 9 | DONE |
| 221 | 36 - Trajectory | Trajectory logging (JSON/JSONL, filter, sort) | `chimera/core/trajectory.py` | 5 | DONE |
| 222 | 36 - Edits | Diff proposal workflow (stage, accept/reject, apply) | `chimera/core/proposed_edit.py` | 8 | DONE |
| 223 | 36 - Compaction | Smart compaction (preserve recent, summarize old) | `chimera/compaction/smart.py` | 3 | DONE |
| 224 | 36 - Index | Codebase indexing with TF-IDF semantic search | `chimera/tools/codebase_index.py` | 6 | DONE |

---

## Phase 37: Gemini CLI + Cursor/Windsurf Gap Closure

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 225 | 37 - Gemini | GroundedSearchTool (search → fetch → cite) | `chimera/tools/grounded_search.py` | 4 | DONE |
| 226 | 37 - Gemini | ContextCache (client-side, hash dedup, LRU) | `chimera/context/cache.py` | 8 | DONE |
| 227 | 37 - Gemini | Image URL support (http/https fetch) | `chimera/tools/image_read.py` | 3 | DONE |
| 228 | 37 - Cursor | EmbeddingIndex (vector search + TF-IDF fallback) | `chimera/tools/embedding_index.py` | 5 | DONE |
| 229 | 37 - Cursor | ApplyMiddleware (intercept writes, stage as proposals) | `chimera/core/apply_middleware.py` | 4 | DONE |
| 230 | 37 - Cursor | ReplCompleter (tab: commands, files, @mentions) | `chimera/cli/completer.py` | 6 | DONE |

---

## Phase 38: Close Codex/Gemini/Aider/OpenCode Gaps

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 231 | 38 - L2 | Head+tail output truncation | `chimera/core/truncation.py` | 4 | DONE |
| 232 | 38 - L2 | Ghost commits (snapshot-based undo) | `chimera/checkpoints_ghost.py` | 5 | DONE |
| 233 | 38 - L4 | Repo map context injection | `chimera/context/repo_map.py` | 4 | DONE |
| 234 | 38 - L1 | Commit message style inference | `chimera/workflows/commit_style.py` | 7 | DONE |
| 235 | 38 - L4 | Structured subagent investigator | `chimera/agents/investigator.py` | 2 | DONE |
| 236 | 38 - L2 | Thought stripping from context | `chimera/compaction/thought_strip.py` | 3 | DONE |
| 237 | 38 - L3 | Response caching (SHA-based dedup) | `chimera/providers/cached.py` | 4 | DONE |
| 238 | 38 - L4 | LSP diagnostics in the agent loop | `chimera/core/lsp_feedback.py` | 2 | DONE |
| 239 | 38 - L1 | File watcher (reactive re-run) | `chimera/env/watcher.py` | 5 | DONE |

## Phase 39: Integration Wiring

| # | Phase | Task | Files | Tests | Status |
|---|-------|------|-------|-------|--------|
| 240 | 39 - Exports | 43 new exports in chimera/__init__.py | `chimera/__init__.py` | — | DONE |
| 241 | 39 - Tools | WebSearchTool + VerifyTool in AGENT_TOOLS (15 total) | `chimera/core/tool_group.py` | — | DONE |
| 242 | 39 - Loop | Truncation wired into tool_executor | `chimera/core/tool_executor.py` | — | DONE |
| 243 | 39 - Loop | Ghost commits wired into tool_executor | `chimera/core/tool_executor.py` | — | DONE |
| 244 | 39 - Events | FileWatcher → EventBus bridge | `chimera/env/watcher.py` | — | DONE |
| 245 | 39 - LoopConfig | truncation + ghost_commits fields | `chimera/core/loop_config.py` | — | DONE |
| 246–256 | 39 - Tests | 25 integration wiring tests | `tests/test_integration_wiring.py` | 25 | DONE |

---

**Total: 2242 tests passing, 49 skipped** (as of 2026-03-19)

Integration tests (skipped without API credentials): 43 tests across multiple files.

---

## Phase Summary

| Phase | Description | Tasks | Tests | Status |
|-------|-------------|-------|-------|--------|
| 1 | Project Scaffold + Core Types | 1–2 | 11 | DONE |
| 2 | Environment Layer | 3–4 | 9 | DONE |
| 3 | Provider Layer | 5–6 | 9 | DONE |
| 4 | Tool Layer | 7–8 | 14 | DONE |
| 5 | Agent Core | 9–11 | 28 | DONE |
| 6 | Synthesis Layer | 12–15 | 81 | DONE |
| 7 | Integration | 16–17 | 3 | DONE |
| 8 | CLI | 18 | 8 | DONE |
| 9 | Extended Tools + Internals | 19–31 | 65 | DONE |
| 10 | Additional Providers | 32–36 | 23 | DONE |
| 11 | Composition, Loops, Strategies | 37–45 | 27 | DONE |
| 12 | Evaluation Layer | 46–51 | 54 | DONE |
| 13 | Environments, CLI, Polish | 52–57 | 36 | DONE |
| 14 | Persistent Shell | 58–64 | 29 | DONE |
| 15 | Cost Tracking | 65–68 | 14 | DONE |
| 16 | synthesize() + CLI | 69–71 | 6 | DONE |
| 17 | Repository Mapping | 72–74 | 13 | DONE |
| 18 | Tree Search Strategy | 75–80 | 20 | DONE |
| 19 | AIMO3 Competition | 81–88 | 42 | DONE |
| 20 | Provider Integration + Docs | 89–94 | 12 | DONE |
| 21 | Battle-Testing | 95–102 | 9 | DONE |
| 22 | API Gaps (Streaming, Async, Step Iter) | 103–109 | 411 | DONE |
| 23 | Infrastructure Hardening | 110–115 | 79 | DONE |
| 24 | Agent Intelligence | 116–120 | 106 | DONE |
| 25 | Serving Layer | 121–123 | 22 | DONE |
| 26 | Production Polish | 124–130 | 73 | DONE |
| 27 | Production Features (15 Features) | 131–147 | 195 | DONE |
| 28 | OpenHands-Inspired Features | 148–159 | 306 | DONE |
| 29 | Documentation Overhaul | 160–165 | — | DONE |
| 30 | Kimi CLI Features | 166–172 | 89 | DONE |
| 31 | ML & Program Synthesis Primitives | 173–185 | 93 | DONE |
| 32 | Real LLM Verification | 186–189 | 11 | DONE |
| 33 | Coding Agent Primitives (Waves 1–5) | 190–202 | 168 | DONE |
| 34 | Production Agent Infrastructure | 203–206 | 43 | DONE |
| 35 | Layer Integration (Prove the Stack) | 207–212 | 6 | DONE |
| 36 | Coding Agent Replication | 213–224 | 71 | DONE |
| 37 | Gemini CLI + Cursor/Windsurf | 225–230 | 30 | DONE |
| 38 | Codex/Gemini/Aider/OpenCode Gaps | 231–239 | 36 | DONE |
| 39 | Integration Wiring | 240–256 | 25 | DONE |

---

## What's Proven with Real LLM (38 tests against GLM-5)

| Feature | Test |
|---------|------|
| Provider text/tools/multi-turn | `test_provider_anthropic_integration.py` |
| Agent creates files + runs them | `test_integration_live.py` |
| Pipeline (coder → reviewer) | `test_integration_live.py` |
| Ensemble (2 agents) | `test_integration_live.py` |
| Supervisor delegation | `test_integration_live.py` |
| CIFixWorkflow | `test_integration_live.py` |
| Session persistence | `test_integration_live.py` |
| Streaming | `test_integration_live.py` |
| Wire monitoring | `test_wire_integration.py` |
| DMailTool rewind | `test_integration_live.py` |
| Flow Skills | `test_integration_live.py` |
| ThinkTool + AskUserTool | `test_integration_live.py` |
| SWE_AGENT preset | `test_presets_e2e.py` |
| AIDER preset | `test_presets_e2e.py` |
| CLINE preset | `test_presets_e2e.py` |
| CODEX preset | `test_presets_e2e.py` |
| Custom preset | `test_presets_e2e.py` |
| synthesize() | `test_integration_live.py` |
| TreeSearch strategy | `test_tree_search_integration.py` |
| MajorityVoting strategy | `test_majority_voting_integration.py` |
| Eval Harness | `test_eval_harness_integration.py` |
| **Agent + EventBus + CostTracker + Middleware** | `test_layer_integration.py` |
| **Pipeline + Events (two-agent)** | `test_layer_integration.py` |
| **synthesize() end-to-end** | `test_layer_integration.py` |
| **AgentPreset + Wire protocol** | `test_layer_integration.py` |
| **Ensemble + shared infrastructure** | `test_layer_integration.py` |
| **Full 6-layer vertical slice** | `test_layer_integration.py` |

## What's NOT Proven with Real LLM (mock-only, pure logic)

Many of these are pure logic modules that don't need an LLM:

- Middleware, MessageQueue, AgentServer (stdlib, no LLM needed)
- FocusChain, HistoryProcessor, ContextMention (pure logic)
- SandboxPolicy, LongTermMemory, InstructionLayer (pure logic)
- TreeSitterParser, DefinitionLookup, DemonstrationPrompt (pure logic)
- Training Curves, Validation Split, Regularization (pure logic)
- CEGIS, Incremental, Tuner, Oracle (mock agent — could use real LLM)
- Fault Localization, Impact, Mutation, Spec Inference (real AST, no LLM)
- ReviewOrchestrator, Researcher, MigrationPlanner, DocGenerator, TestGenerator (mock agent)
- WebSearchTool, InteractiveApprover, ProjectDiscovery, Microagent, AgentController (pure logic)
- Trajectory, ProposedEdit, SmartCompaction, CodebaseIndex (pure logic)

---

## Agent Replication Status

| Agent | Status | Primitives |
|-------|--------|------------|
| SWE-Agent | **Full** | RetryLoop, SWE_TOOLS, DemonstrationPrompt, trajectory logging |
| Aider | **Full** | LintFeedbackLoop, TreeSitter, Git, RepoMap, commit style inference, file watcher |
| Cline | **Full** | PlanActLoop, FocusChain, DefinitionLookup, InstructionLayer, interactive approval |
| Codex CLI | **Full** | SandboxPolicy (Docker enforced), ghost commits, head+tail truncation, response caching, prompt caching |
| OpenHands | **Full** | AgentController FSM, microagents, ACP, Critic, Docker, EventBus |
| Gemini CLI | **Full** | WebSearch, GroundedSearch, ContextCache, thought stripping, subagent investigator, extended thinking |
| OpenCode | **Full** | LSP feedback middleware, SemanticSearch, ApplyMiddleware |
| Kimi CLI | **Full** | Wire protocol, DMailTool, Flow skills, ThinkTool, multi-provider |

## What's Next

- [x] ~~Layer integration demo~~ (Phase 35)
- [x] ~~Close coding agent gaps~~ (Phases 36–38)
- [x] ~~Wire all modules into loop + exports~~ (Phase 39)
- [ ] SWE-bench end-to-end run (prove Chimera can fix real GitHub issues)
- [ ] Analyze remaining agents: Qwen Code, AiChat, AutoGPT, MetaGPT, Continue, DeepCode
- [ ] Verify ML/synthesis primitives with real LLM synthesis runs
- [ ] Verify remaining workflows (Review, Research, Migration, DocGen, TestGen) with real LLM
- [ ] PyPI publishing
- [ ] CI/CD pipeline (GitHub Actions)
- [ ] Full agent replication benchmarks on real tasks

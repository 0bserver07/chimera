# Chimera — Task Status

> 101 tasks across 22 phases. TDD approach: tests first, then implementation.
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

**Total: 824 tests passing, 20 skipped** (as of 2026-02-26, Phases 1-21 only)

!!! note "Current test count"
    The project now has 1780+ tests (as of 2026-03-16). The 824 count above reflects only Phases 1-21. Later phases (production features, workflows, CLI, Kimi features) added ~1000 additional tests.

Integration tests (skipped without API credentials): 20 tests across 4 files.

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
| 18 | Tree Search Strategy | 75–80 | 19+1 | DONE |
| 19 | AIMO3 Competition | 81–88 | 42 | DONE |
| 20 | Provider Integration + Docs | 89–94 | 12 | DONE |
| 21 | Battle-Testing | 95–102 | 8 | DONE |

---

## What's Next (not yet planned)

- [ ] Docker environment integration tests
- [ ] Plugin/extension system
- [ ] Repository mapping enhancements (non-Python languages)
- [ ] Multi-file edit transactions
- [ ] Cost tracking: add GLM-5 / custom model pricing
- [ ] Documentation site
- [ ] Make Ensemble composition actually parallel (ThreadPoolExecutor + env isolation)

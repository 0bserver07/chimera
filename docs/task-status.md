# Chimera — Task Status

> 66 tasks across 14 phases. TDD approach: tests first, then implementation.
> Sources: `docs/plans/2026-02-20-chimera-implementation-plan.md`, `docs/plans/2026-02-20-chimera-extension-plan.md`, `docs/plans/2026-02-22-persistent-shell-plan.md`

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

**Total: 396 tests passing** (as of 2026-02-22)

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

---

## What's Next (not yet planned)

- [ ] Real provider integration tests (with API keys)
- [ ] Docker environment integration tests
- [ ] `chimera.synthesize()` one-liner with provider auto-detection
- [ ] Plugin/extension system
- [ ] Repository mapping (aider-style)
- [ ] Multi-file edit transactions
- [ ] Cost tracking and budgets
- [ ] Documentation site

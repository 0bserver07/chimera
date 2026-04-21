# Chimera - Session Context

## What Is Chimera

A composable coding agent framework. A Python framework that lets you **synthesize codebases from specifications** using AI agents. Named after the mythological shape-shifting creature.

Core verb: `.synthesize()` -- combining three meanings:
- **CS**: Program synthesis (generating programs from specs)
- **Biology**: Chimera synthesis (combining distinct organisms)
- **Chemistry**: Synthesis (combining elements into compounds)

Origin: The insight (from ML theory) that agentic coding is essentially ML -- the engineer defines a spec (loss function), agents iterate (training), and the result is a codebase (trained model) you deploy without inspecting internals.

## Key Design Decisions

| Decision | Choice |
|----------|--------|
| Name | Chimera |
| Identity | Both agent toolkit AND synthesis framework |
| Users | Framework authors, developers, researchers |
| Language | Python 3.11+ |
| Core verb | `.synthesize()` |
| Synthesis default | Test-driven convergence |
| Other strategies | Curriculum, Ensemble, Passthrough |
| Package style | Monolithic layered (single pip install) |
| Dependencies | Zero required; providers are optional extras |
| Starting point | Fresh codebase, porting ideas from NovalisCode, Pi, OpenCode, coding-agents |

## The 6-Layer Stack

```
Layer 6: CLI          -- chimera synthesize / chimera eval / chimera bench
Layer 5: Synthesis    -- Trainer, Strategy, Spec, Architecture, Constraint
Layer 4: Evaluation   -- Harness, Metrics, AntiOverfit, Benchmarks (SWE-bench, HumanEval, Custom)
Layer 3: Agent        -- Agent, Tool, Loop (ReAct, PlanAndExecute, Reflexion, TreeOfThought), Prompt, Context
Layer 2: Provider     -- LLM backends (Claude, GPT, Gemini, Ollama, OpenAI-compatible)
Layer 1: Environment  -- Local, Docker, Git (+ persistent shell via tmux)
```

## API at Three Levels

```python
# End user (one-liner)
result = chimera.synthesize("Build a REST API for tasks", tests="./tests/")

# Developer (configured)
trainer = chimera.Trainer(arch, spec, agent=chimera.Agent(provider=chimera.Claude()))
result = trainer.synthesize(strategy=chimera.TestConvergence())

# Framework author (subclassing)
class MyAgent(chimera.Agent):
    tools = [chimera.tools.read, chimera.tools.edit, MyCustomTool()]
    loop = chimera.loops.ReAct(max_steps=100)

# Researcher (benchmarking)
harness = chimera.eval.Harness(chimera.benchmarks.SWEBenchLite())
results = harness.run(agent=MyAgent())
```

## Implementation Progress

### Phase 1: Project Scaffold + Core Types -- DONE
- [x] pyproject.toml (chimera-ai, zero deps)
- [x] chimera/types.py (Message, ToolCall, ToolResult, CommandResult, TestResult, StepResult, AgentResult)
- [x] 11 tests passing

### Phase 2: Environment Layer -- DONE
- [x] chimera/env/base.py (Environment ABC)
- [x] chimera/env/local.py (LocalEnvironment with file ops, commands, checkpointing)
- [x] 9 tests passing

### Phase 3: Provider Layer -- DONE
- [x] chimera/providers/base.py (Provider ABC, Response, StreamEvent)
- [x] chimera/providers/anthropic.py (AnthropicProvider with tool use)
- [x] 9 tests passing

### Phase 4: Tool Layer -- DONE
- [x] chimera/core/tool.py (BaseTool, tool decorator, schema conversion)
- [x] chimera/tools/ (read_file, write_file, bash)
- [x] 14 tests passing

### Phase 5: Agent Core -- DONE
- [x] chimera/core/context.py (Context)
- [x] chimera/core/prompt.py (Prompt with {{variable}} templates)
- [x] chimera/core/loop.py (ReAct loop)
- [x] chimera/core/agent.py (Agent = Provider + Tools + Loop + Prompt)
- [x] 28 tests passing

### Phase 6: Synthesis Layer -- DONE
- [x] chimera/training/spec.py (Spec from string/file/tests)
- [x] chimera/training/architecture.py (Architecture, Layer with deps, topological sort)
- [x] chimera/training/constraint.py (tests_pass, min_pass_rate, max_files, max_total_lines, custom, no_syntax_errors, max_complexity, no_security_issues)
- [x] chimera/training/strategies/base.py (Strategy ABC, EpochResult, SynthesisResult, Callback)
- [x] chimera/training/strategies/convergence.py (TestConvergence with checkpointing/rollback)
- [x] chimera/training/trainer.py (Trainer)
- [x] chimera/training/callbacks.py (CostLimit, EpochCheckpoint, HistoryRecorder, ProgressBar)
- [x] 81 tests passing

### Phase 7: Integration -- DONE
- [x] chimera/__init__.py (public API with 60+ exports)
- [x] tests/test_integration.py (end-to-end with mock provider, gradual convergence, one-liner)
- [x] 3 tests passing

### Phase 8: CLI -- DONE
- [x] chimera/cli/main.py (argparse with synthesize/synth/eval/bench subcommands)
- [x] 8 tests passing

### Phase 9: Extended Tools + Internals -- DONE
- [x] chimera/tools/edit.py (EditFileTool -- exact string replacement)
- [x] chimera/tools/search.py (SearchTool -- regex across files)
- [x] chimera/tools/list_files.py (ListFilesTool -- directory listing with glob)
- [x] chimera/tools/test.py (TestTool -- run pytest from agent)
- [x] chimera/tools/web_fetch.py (WebFetchTool -- HTTP fetch, requires httpx)
- [x] chimera/tools/git.py (GitTool -- git commands with destructive blocking)
- [x] chimera/tools/replace_in_file.py (ReplaceInFileTool -- regex replace)
- [x] chimera/tools/delegate.py (DelegateTool -- sub-agent dispatch)
- [x] chimera/core/approval.py (ApprovalPolicy, AutoApprove, AlwaysDeny, AllowList)
- [x] chimera/core/tool_group.py (ToolGroup, DEFAULT_TOOLS)
- [x] chimera/core/loop_detection.py (LoopDetector -- sliding window MD5 signatures)
- [x] chimera/core/compression.py (ContextCompressor -- keep-first/keep-last)
- [x] chimera/core/streaming.py (StreamHandler, PrintStreamHandler, CollectStreamHandler)
- [x] 65 tests passing

### Phase 10: Additional Providers -- DONE
- [x] chimera/providers/openai.py (OpenAIProvider -- Chat Completions API)
- [x] chimera/providers/google.py (GoogleProvider -- Gemini format conversion)
- [x] chimera/providers/ollama.py (OllamaProvider -- httpx against /api/chat)
- [x] chimera/providers/compatible.py (OpenAICompatibleProvider -- OpenRouter/vLLM/Groq)
- [x] chimera/providers/factory.py (create_provider() with model name inference)
- [x] 23 tests passing

### Phase 11: Composition, Loops, Strategies -- DONE
- [x] chimera/composition/pipeline.py (Pipeline -- sequential agent chaining)
- [x] chimera/composition/ensemble.py (Ensemble -- parallel with best() selector)
- [x] chimera/composition/supervisor.py (Supervisor -- coordinator + workers)
- [x] chimera/core/loops/plan_execute.py (PlanAndExecute)
- [x] chimera/core/loops/reflexion.py (Reflexion with reflection prompts)
- [x] chimera/core/loops/tree_of_thought.py (TreeOfThought with candidate evaluation)
- [x] chimera/training/strategies/curriculum.py (CurriculumStrategy -- topological sort)
- [x] chimera/training/strategies/ensemble.py (EnsembleStrategy -- multiple attempts)
- [x] chimera/training/strategies/passthrough.py (Passthrough -- single-shot)
- [x] 27 tests passing

### Phase 12: Evaluation Layer -- DONE
- [x] chimera/eval/harness.py (Harness, Benchmark ABC, TaskEvalResult, EvalResult)
- [x] chimera/eval/metrics.py (pass_at_k, avg_cost, avg_steps, resolve_rate)
- [x] chimera/eval/anti_overfit.py (OverfitSignal, check_output_similarity, check_hardcoded_answers)
- [x] chimera/eval/benchmarks/swe_bench.py (SWEBench adapter)
- [x] chimera/eval/benchmarks/human_eval.py (HumanEval adapter)
- [x] chimera/eval/benchmarks/custom.py (CustomBenchmark)
- [x] 54 tests passing

### Phase 13: Environments, CLI, Polish -- DONE
- [x] chimera/env/docker.py (DockerEnvironment with container lifecycle)
- [x] chimera/env/git_env.py (GitEnvironment -- git-based checkpointing)
- [x] chimera/cli/main.py (eval and bench subcommands)
- [x] chimera/training/constraint.py (no_syntax_errors, max_complexity, no_security_issues)
- [x] chimera/training/callbacks.py (ProgressBar)
- [x] 36 tests passing

### Phase 14: Persistent Shell -- DONE
- [x] chimera/env/session.py (SessionMixin -- tmux-based persistent shell sessions)
- [x] Named shells (create, list) with independent tmux windows
- [x] run_in_session() with sentinel-based output capture and polling
- [x] Environment ABC updated with shell_name parameter
- [x] LocalEnvironment integration (session=True routes through tmux)
- [x] GitEnvironment inherits session support via MRO
- [x] SessionMixin exported from chimera.env and chimera packages
- [x] 29 tests passing

### Phase 15: Cost Tracking -- DONE
- [x] chimera/providers/cost.py (PRICING table, calculate_cost)
- [x] Cost aggregation in ReAct, PlanAndExecute, Reflexion, TreeOfThought loops
- [x] Cost propagation through Trainer → SynthesisResult
- [x] 14 tests passing

### Phase 16: synthesize() One-Liner + CLI -- DONE
- [x] chimera/synthesize.py (one-liner: spec + model → SynthesisResult)
- [x] CLI run_synthesize() wired to real synthesis logic
- [x] Exported from chimera package
- [x] 6 tests passing

### Phase 17: Repository Mapping -- DONE
- [x] chimera/tools/repo_map.py (RepoMap with ast-based Python analysis)
- [x] RepoMapTool (agent-usable tool)
- [x] Exported from chimera and chimera.tools packages
- [x] 13 tests passing

### Phase 18: Tree Search Strategy -- DONE
- [x] chimera/training/strategies/tree_search.py (SearchNode, TreeSearch, _clone_environment)
- [x] Best-first search with parallel branch execution via ThreadPoolExecutor
- [x] Environment cloning for isolated branch evaluation
- [x] Custom branch_fn support, pruning, cost limits, callbacks
- [x] Exported from chimera and chimera.training.strategies packages
- [x] 19 tests passing

## Test Count: 3953 passing (v0.3.0)

## Key Files

- Design doc: `docs/plans/2026-02-20-chimera-framework-design.md`
- Implementation plan (phases 1-8): `docs/plans/2026-02-20-chimera-implementation-plan.md`
- Extension plan (phases 9-13): `docs/plans/2026-02-20-chimera-extension-plan.md`
- Persistent shell plan (phase 14): `docs/plans/2026-02-22-persistent-shell-plan.md`
- This file: `CONTEXT.md`

## Ideas Ported From

| Source | What |
|--------|------|
| NovalisCode | D-Mail checkpointing, 3-tier loop detection, smart edit, benchmark harness |
| NovalisGraph | Node composition, graph routing |
| Pi | Streaming tool output, extension architecture, session branching |
| OpenCode | Permission system (allow/deny/ask), DOOM_LOOP detection, multi-agent dispatch |
| SWE-agent | ReAct loop, Docker sandbox, thought-action separation |
| Aider | Edit format variety, repository mapping |
| MetaGPT | Role-based multi-agent (Supervisor pattern) |
| Framework design | Layer/Model/compile/fit paradigm, callbacks, zero-dep core |
| ML theory | Anti-overfitting, codebase as trained model, spec as loss function |


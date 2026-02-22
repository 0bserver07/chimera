# Chimera - Session Context

## What Is Chimera

The Keras of agentic coding. A Python framework that lets you **synthesize codebases from specifications** using AI agents. Named after the mythological shape-shifting creature.

Core verb: `.synthesize()` -- combining three meanings:
- **CS**: Program synthesis (generating programs from specs)
- **Biology**: Chimera synthesis (combining distinct organisms)
- **Chemistry**: Synthesis (combining elements into compounds)

Origin: Francois Chollet's insight that agentic coding is essentially ML -- the engineer defines a spec (loss function), agents iterate (training), and the result is a codebase (trained model) you deploy without inspecting internals.

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
| Package style | Monolithic Keras-style (single pip install) |
| Dependencies | Zero required; providers are optional extras |
| Starting point | Fresh codebase, porting ideas from NovalisCode, Pi, OpenCode, coding-agents |

## The 6-Layer Stack

```
Layer 6: CLI          -- chimera synthesize / chimera eval / chimera bench
Layer 5: Synthesis    -- Trainer, Strategy, Spec, Architecture, Constraint
Layer 4: Evaluation   -- Harness, Metrics, AntiOverfit
Layer 3: Agent        -- Agent, Tool, Loop, Prompt, Context
Layer 2: Provider     -- LLM backends (Claude, GPT, Gemini, local)
Layer 1: Environment  -- Sandbox, Filesystem, Docker, Git, TestRunner
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
- [x] chimera/training/constraint.py (tests_pass, min_pass_rate, max_files, max_total_lines, custom)
- [x] chimera/training/strategies/base.py (Strategy ABC, EpochResult, SynthesisResult, Callback)
- [x] chimera/training/strategies/convergence.py (TestConvergence with checkpointing/rollback)
- [x] chimera/training/trainer.py (Trainer)
- [x] chimera/training/callbacks.py (CostLimit, EpochCheckpoint, HistoryRecorder)
- [x] 81 tests passing (spec: 21, constraints: 30, strategy: 16, trainer: 14)

### Phase 7: Integration -- DONE
- [x] chimera/__init__.py (public API with 35 exports + chimera.synthesize() one-liner)
- [x] tests/test_integration.py (end-to-end with mock provider, gradual convergence, one-liner)
- [x] 3 tests passing

### Phase 8: CLI -- DONE
- [x] chimera/cli/main.py (argparse with synthesize/synth subcommand)
- [x] 8 tests passing

## Test Count: 163 passing (all phases complete)

## Key Files

- Design doc: `docs/plans/2026-02-20-chimera-framework-design.md`
- Implementation plan: `docs/plans/2026-02-20-chimera-implementation-plan.md`
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
| Keras | Layer/Model/compile/fit paradigm, callbacks, zero-dep core |
| Chollet | Anti-overfitting, codebase as trained model, spec as loss function |

## Related Projects (same workspace)

- `/Users/yadkonrad/dev_dev/year26/feb26/NovalisCode` -- AI coding assistant (existing)
- `/Users/yadkonrad/dev_dev/year26/feb26/NovalisGraph` -- Graph framework (existing, was KayGraph)
- `/Users/yadkonrad/dev_dev/year26/feb26/pi-projects` -- Pi coding agent (reference)
- `/Users/yadkonrad/dev_dev/year26/feb26/opencode` -- OpenCode agent (reference)
- `/Users/yadkonrad/dev_dev/year25/nov25/coding-agents` -- Collection of 30+ coding agents (reference)

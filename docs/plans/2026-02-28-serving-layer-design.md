# Chimera Serving Layer Design

**Goal:** Complete the framework analogy — `synthesize()` is `.fit()`, now add `.evaluate()` (real eval CLI) and interactive serving (`chimera code` REPL).

**Architecture:** 3 changes that build on each other: merge streaming into ReAct (enabler), wire eval CLI (standalone), add code REPL (uses merged streaming).

**Tech Stack:** Python 3.11+, stdlib only (no new dependencies)

---

## Context

Chimera has a complete framework layer: 13 tools, 5 loops, 6 providers, Session with multi-turn + persistence, streaming handlers, eval harness with 4 benchmarks. Two gaps remain:

1. **`chimera eval`** — The eval layer (Harness, Benchmarks, metrics) is fully implemented but the CLI stubs don't call it.
2. **`chimera code`** — No interactive REPL. The pieces exist (Session.iter_chat, ConsoleStreamHandler, ProjectConfig) but aren't wired together.

A prerequisite for a good REPL is that streaming and permissions/detection live in the same loop. Currently they're split across `ReAct` (permissions, detection, iter_steps) and `StreamingReAct` (streaming, no iter_steps). This must be merged first.

---

## Component 1: Merge Streaming into ReAct

### Problem

Two parallel loop implementations that don't compose:

| Feature | ReAct | StreamingReAct |
|---------|-------|----------------|
| Streaming (provider.stream()) | No | Yes |
| Permissions/detection | Yes | No |
| iter_steps() generator | Yes | No |
| LoopConfig integration | Yes | No |

### Solution

When `LoopConfig.handler` is set, `ReAct.iter_steps()` uses `provider.stream()` instead of `provider.complete()`. The `_accumulate_stream()` static method moves from `StreamingReAct` to `ReAct`.

### Changes

**`chimera/core/loop.py`** — In `iter_steps()`, the provider call (line 63) becomes conditional:

```python
handler = self.config.handler if self.config else None
if handler:
    handler.on_step_start(steps)
    events = provider.stream(context.to_messages(), tools=schemas or None)
    response = self._accumulate_stream(events, handler)
    handler.on_step_end(steps)
else:
    response = provider.complete(context.to_messages(), tools=schemas or None)
```

Tool execution gains handler callbacks:
```python
if handler:
    handler.on_tool_start(tc.name, tc.id)
# ... existing tool execution via execute_tool_calls_incremental ...
if handler:
    handler.on_tool_end(tc.id, content)
```

Add `_accumulate_stream()` as a static method on `ReAct` (copied from `StreamingReAct._accumulate_stream` — identical logic).

At the end of the loop (done or max_steps), call `handler.on_done()` if handler is set.

### Behavior

- `LoopConfig.handler = None` (default): identical to current behavior. `provider.complete()` used, no streaming callbacks.
- `LoopConfig.handler = ConsoleStreamHandler()`: text streams to stdout token-by-token while all LoopConfig features (permissions, detection, compaction, events) remain active.
- `Session.iter_chat()` automatically gains streaming because it delegates to the agent's loop.
- `StreamingReAct` becomes unnecessary but kept for backward compatibility.

---

## Component 2: Wire chimera eval CLI

### Problem

`run_eval()` and `run_bench()` in `chimera/cli/main.py` are print-only stubs. The eval layer (Harness, 4 Benchmarks, metrics, anti-overfit) is fully implemented.

### Solution

Connect the CLI stubs to the existing eval classes. Add `--model` flag. Add benchmark name-to-class registry. Serialize results to JSON.

### Changes

**`chimera/cli/main.py`:**

Add `--model` to eval and bench subparsers.

Add benchmark registry:
```python
_BENCHMARKS = {
    "human-eval": "chimera.eval.benchmarks.human_eval:HumanEval",
    "swe-bench": "chimera.eval.benchmarks.swe_bench:SWEBench",
    "aimo": "chimera.eval.benchmarks.aimo:AIMOBenchmark",
    "custom": "chimera.eval.benchmarks.custom:CustomBenchmark",
}
```

`run_eval()`:
1. Look up benchmark class from `--benchmark` name via registry.
2. Instantiate benchmark (pass `--dataset` path if provided, `--limit` for task slicing).
3. Create `Provider` from `--model`, create `Agent` with `DEFAULT_TOOLS`.
4. Create `Harness(benchmark, agent)`, call `harness.run()`.
5. Print summary to stderr (benchmark name, passed/total, pass rate, cost).
6. Write `EvalResult` as JSON to `--output` if provided.

`run_bench()`:
1. Create `CustomBenchmark` from `--tasks-dir`.
2. Same flow as `run_eval()`.

Add helper `_result_to_dict(result: EvalResult) -> dict` using `dataclasses.asdict()` for JSON serialization.

Add helper `_load_benchmark(name, dataset, limit)` that imports the class, instantiates it, and optionally slices tasks.

---

## Component 3: chimera code REPL

### Problem

No interactive coding agent. All building blocks exist but nobody wires them into a REPL.

### Solution

New `chimera/cli/code.py` module (~80 lines) with a `run_code()` function. Minimal stdin/stdout REPL using `Session.iter_chat()` with `ConsoleStreamHandler` for streaming.

### Changes

**New file `chimera/cli/code.py`:**

```python
def run_code(args) -> int:
    # 1. Create provider and environment
    provider = create_provider(args.model)
    env = LocalEnvironment(workdir=os.path.abspath(args.workdir))
    env.setup()

    # 2. Auto-discover project context
    project = ProjectConfig.from_directory(args.workdir)
    rules = project.rules_text if project else ""

    # 3. Build agent with streaming
    handler = ConsoleStreamHandler()
    loop = ReAct(max_steps=args.max_steps, config=LoopConfig(handler=handler))
    tools = list(DEFAULT_TOOLS)

    system = "You are a coding assistant. ..."
    if rules:
        system += "\n\n# Project Context\n" + rules
    prompt = Prompt.from_string(system)

    agent = Agent(provider=provider, tools=tools, loop=loop, prompt=prompt)
    session = Session(agent=agent, env=env)

    # 4. REPL loop
    total_cost = 0.0
    print(f"chimera code v{__version__} | model: {args.model} | workdir: {args.workdir}")
    print("Type /exit to quit.\n")

    while True:
        try:
            user_input = input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        cmd = user_input.strip()
        if cmd in ("/exit", "/quit"):
            break
        if not cmd:
            continue

        result = drain_steps(session.iter_chat(user_input))
        total_cost += result.cost
        print(f"  [cost: ${result.cost:.4f} | steps: {result.steps}]")

    print(f"\nSession total: ${total_cost:.4f}")
    env.cleanup()
    return 0
```

**`chimera/cli/main.py`:**

Add `code` subcommand to the parser:
```python
code_parser = subparsers.add_parser("code", help="Interactive coding agent")
code_parser.add_argument("--model", default="claude-sonnet-4-20250514")
code_parser.add_argument("--workdir", default=".")
code_parser.add_argument("--max-steps", type=int, default=50)
```

Add to `main()`:
```python
elif args.command == "code":
    from chimera.cli.code import run_code
    return run_code(args)
```

### Key Design Decisions

- **`drain_steps()` for permission handling** — auto-denies by default. Interactive permission approval is a future enhancement.
- **Streaming via handler inside the loop** — the REPL just calls `drain_steps()` and the handler prints text as it streams. No streaming logic in the REPL itself.
- **`ProjectConfig.from_directory()`** — auto-injects CLAUDE.md/AGENTS.md into the system prompt. Works fine with no config.
- **Multi-turn via `Session`** — context carries across turns automatically.
- **Cost tracking** — per-turn and cumulative, printed on exit.

### The "OpenCode in 14 lines" demo

```python
from chimera import Agent, create_provider, DEFAULT_TOOLS, LocalEnvironment, ReAct, Session, drain_steps
from chimera.core.loop_config import LoopConfig
from chimera.streaming import ConsoleStreamHandler

agent = Agent(
    provider=create_provider("claude-sonnet-4-20250514"),
    tools=list(DEFAULT_TOOLS),
    loop=ReAct(config=LoopConfig(handler=ConsoleStreamHandler())),
)
env = LocalEnvironment(workdir=".")
env.setup()
session = Session(agent=agent, env=env)

while True:
    task = input("> ")
    if task in ("/exit", "/quit"): break
    drain_steps(session.iter_chat(task))
```

---

## What This Completes

After implementation:

```
Framework                       Chimera
─────────────────────────────────────────────────
model.fit()                    synthesize()           ✓ exists
model.predict()                agent.run()            ✓ exists
model.evaluate()               chimera eval           ✓ wired
model.fit(callbacks=...)       Trainer + Callbacks    ✓ exists
tf.serving                     chimera code           ✓ interactive REPL
model layers                   Tools, Loops           ✓ exists
model.compile(optimizer=...)   Agent(loop=ReAct())    ✓ exists
```

The framework becomes complete: batch synthesis, evaluation, and interactive serving — all sharing the same Agent/Provider/Tool/Loop stack.

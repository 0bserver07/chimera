# Chimera Framework Design

**Date**: 2026-02-20
**Status**: Approved
**Vision**: A composable coding agent framework

---

## Origin

> *"Sufficiently advanced agentic coding is essentially machine learning: the engineer sets up the optimization goal (the spec), then an optimization process (coding agents) iterates until the goal is reached. The result is a blackbox model (the generated codebase): an artifact that performs the task, that you deploy without ever inspecting its internal logic."*


Chimera is the framework that makes this real. Named after the mythological creature that takes many forms, Chimera adapts to what you need: a toolkit for building coding agents, a synthesis framework for generating codebases from specs, or a benchmark harness for evaluating agent performance. The core verb is `.synthesize()` -- combining CS program synthesis, biological chimera synthesis, and chemical synthesis into one concept.

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Identity | Both toolkit AND training framework | Low-level primitives for framework authors, high-level API for end users |
| Users | Framework authors, developers, researchers | Three API levels, like a layered framework (subclassing, functional, sequential) |
| Language | Python | Matches ML ecosystem, framework analogy, SWE-bench, existing code |
| Synthesis | Test-driven convergence (default) | Plus: curriculum, ensemble, passthrough strategies |
| Core verb | `.synthesize()` | Program synthesis + biological chimera + chemical synthesis |
| Architecture | Monolithic layered | Single package, layered API. Split later if needed |
| Starting point | Fresh codebase | Port best ideas from NovalisCode, NovalisGraph, Pi, coding-agents |

---

## The Stack

Six layers, each usable independently:

```
+---------------------------------------------------+
|  Layer 6: CLI                                      |
|  chimera synthesize / chimera eval / chimera bench  |
|  Target: End users                                 |
+---------------------------------------------------+
|  Layer 5: Synthesis                                |
|  Trainer, Strategy, Spec, Architecture, Constraint |
|  Target: Developers                                |
+---------------------------------------------------+
|  Layer 4: Evaluation                               |
|  Harness, Metrics, AntiOverfit                     |
|  Target: Researchers                               |
+---------------------------------------------------+
|  Layer 3: Agent                                    |
|  Agent, Tool, Loop, Prompt, Context                |
|  Target: Framework authors                         |
+---------------------------------------------------+
|  Layer 2: Provider                                 |
|  LLM backends (Claude, GPT, Gemini, local)         |
|  Target: Infrastructure                            |
+---------------------------------------------------+
|  Layer 1: Environment                              |
|  Sandbox, Filesystem, Docker, Git, TestRunner      |
|  Target: Infrastructure                            |
+---------------------------------------------------+
```

Each layer depends only on layers below it. You can use Layer 3 without Layer 5. Just similarly -- you don't need `model.compile()` to use a `Dense` layer.

---

## Layer 1: Environment

Where generated code lives and gets tested. Like the hardware backend in ML.

```python
from chimera import Environment

# Local development (default)
env = Environment.local(
    workdir="./myapp",
    test_cmd="pytest",
    git=True,
)

# Docker sandbox (for benchmarks, untrusted code)
env = Environment.docker(
    image="python:3.12",
    test_cmd="pytest",
    timeout=300,
    memory_limit="2g",
)

# Custom environment
class MyEnvironment(Environment):
    def setup(self) -> None: ...
    def run_tests(self) -> TestResult: ...
    def checkpoint(self) -> str: ...
    def restore(self, checkpoint_id: str) -> None: ...
    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> None: ...
    def run_command(self, cmd: str) -> CommandResult: ...
    def cleanup(self) -> None: ...
```

Key ideas ported:
- **NovalisCode D-Mail**: Checkpointing built into environment, not bolted on
- **SWE-agent**: Docker isolation for benchmarks and untrusted code

---

## Layer 2: Provider

LLM backends. Protocol-based -- any class that implements `complete()` and `stream()` works.

```python
from chimera import Provider

claude = Provider.anthropic(model="claude-sonnet-4-20250514")
gpt    = Provider.openai(model="gpt-4.1")
gemini = Provider.google(model="gemini-2.5-pro")
local  = Provider.ollama(model="qwen2.5-coder:32b")
custom = Provider.openai_compatible(base_url="http://localhost:8080/v1")

class Provider(Protocol):
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None) -> Response: ...
    async def stream(self, messages, tools=None, **kwargs) -> AsyncIterator[StreamEvent]: ...
    @property
    def context_window(self) -> int: ...
    @property
    def supports_tool_use(self) -> bool: ...
```

Uses existing SDKs (`anthropic`, `openai`, `google-genai`) under the hood.

---

## Layer 3: Agent

The composable primitives. Every coding agent decomposes into: Agent = Provider + Tools + Loop + Prompt + Context.

### Tool

```python
class Tool(Protocol):
    name: str
    description: str
    parameters: dict  # JSON Schema

    def execute(self, args: dict, env: Environment) -> ToolResult: ...

# Built-in tools
from chimera.tools import read_file, write_file, edit_file, search, bash, list_files, test

# Custom tool
@chimera.tool(name="run_linter", description="Run linter", parameters={"fix": bool})
def run_linter(args, env):
    return ToolResult(output=env.run_command(f"ruff check {'--fix' if args['fix'] else ''} ."))
```

### Tool features

- **Streaming output**: Tools yield `ToolUpdate` for real-time progress (from Pi)
- **Approval workflows**: Pattern-matched permissions with allow/deny/ask (from OpenCode)
- **Tool groups**: Bundle tools by capability level (from SWE-agent)
- **Smart edit**: 3-tier editing with automatic strategy selection (from NovalisCode)
- **Dependencies**: Tools can declare they require other tools

```python
# Approval rules
approval = Approval(rules={
    "bash": "ask",
    "read_file": "allow",
    "write_file": {"*.env": "deny", "tests/*": "allow", "*": "ask"},
})

# Tool groups
readonly = ToolGroup("readonly", [read_file, search, list_files])
editing  = ToolGroup("editing", [write_file, edit_file])
```

### Loop

```python
class Loop(Protocol):
    def run(self, agent, context, env) -> AgentResult: ...

from chimera.loops import ReAct, PlanAndExecute, TreeOfThought, Reflexion
```

### Context

```python
class Context:
    messages: list[Message]
    files: dict[str, str]
    checkpoints: list[str]
    metrics: dict[str, float]

    def compress(self) -> None: ...
    def add_tool_result(self, tool, result) -> None: ...
```

### Agent

```python
class Agent:
    provider: Provider
    tools: list[Tool]
    loop: Loop
    prompt: Prompt

    def step(self, context, env) -> StepResult: ...
    def run(self, task, env) -> AgentResult: ...

agent = Agent(
    provider=Provider.anthropic("claude-sonnet-4-20250514"),
    tools=[read_file, write_file, edit_file, search, bash, test],
    loop=ReAct(max_steps=50),
)
result = agent.run(task="Fix the bug in auth.py", env=Environment.local("./project"))
```

### Agent Composition

```python
from chimera import Pipeline, Ensemble, Supervisor
from chimera.tools import delegate

# Sequential pipeline
pipeline = Pipeline([
    Agent("planner", tools=[read_file, search]),
    Agent("coder", tools=[read_file, write_file, edit_file, bash]),
    Agent("reviewer", tools=[read_file, search, test]),
])

# Sub-agent delegation (as a tool)
research_tool = delegate(
    Agent("researcher", tools=[read_file, search, web_fetch]),
    description="Delegate a research question to a specialist",
)
coder = Agent(tools=[read_file, edit_file, bash, research_tool])

# Parallel ensemble (multiple agents, pick best)
ensemble = Ensemble(
    agents=[Agent("conservative", temperature=0.0), Agent("creative", temperature=0.7)],
    selector=chimera.selectors.most_tests_pass,
)

# Supervisor (coordinator + workers)
supervisor = Supervisor(
    coordinator=Agent("lead", prompt="You manage a team..."),
    workers={"frontend": Agent(...), "backend": Agent(...), "qa": Agent(...)},
)
```

---

## Layer 4: Evaluation

The bridge between agents and training. What makes Chimera more than "just another agent framework."

```python
from chimera.eval import Harness, Metric
from chimera.benchmarks import SWEBenchLite, TerminalBench, HumanEval, Custom

harness = Harness(
    benchmark=SWEBenchLite(),
    agent=my_agent,
    env=Environment.docker("python:3.12"),
    metrics=[Metric.resolve_rate, Metric.cost_per_task, Metric.iterations_per_task],
)
results = harness.run(split="test", max_tasks=50)
```

### Anti-overfitting

From the insight about Clever Hans solutions:

```python
from chimera.eval import MutationTesting, AdversarialSpecs, CrossValidation, DriftDetection

evaluators = [
    MutationTesting(min_kill_rate=0.85),
    AdversarialSpecs(generator=provider, n_cases=10),
]
```

---

## Layer 5: Training

The synthesis layer. Where everything comes together.

### Spec (the "loss function")

```python
spec = Spec("A REST API for task management with CRUD, pagination, filtering")
spec = Spec.from_file("spec.md")
spec = Spec.from_tests("./tests/")  # Tests ARE the spec
```

### Architecture (the "model structure")

Four levels of prescriptiveness:

```python
# Abstract (agent decides everything)
Layer("data")

# Guided (constrained)
Layer("data", description="SQLite using stdlib", constraints=["No ORM"])

# Templated (skeleton provided)
Layer("data", template="./templates/data.py")

# Frozen (don't modify)
Layer("config", frozen=True, code="./config.py")

# With dependencies
arch = Architecture([
    Layer("models"),
    Layer("storage", depends_on=["models"]),
    Layer("api", depends_on=["storage"]),
])
```

### Constraints ("regularization")

```python
constraints = [
    Constraint.tests_pass,
    Constraint.coverage(min=0.8),
    Constraint.max_complexity(10),
    Constraint.no_security_vulnerabilities,
    Constraint.max_files(20),
]
```

### Trainer

```python
trainer = Trainer(
    architecture=arch,
    spec=spec,
    agent=Agent(provider=Provider.anthropic("claude-sonnet-4-20250514")),
    constraints=constraints,
    env=Environment.local("./output", test_cmd="pytest"),
)

result = trainer.synthesize(
    strategy=TestConvergence(max_iterations=100, patience=5),
    callbacks=[Checkpoint(every=5), CostLimit(max=10.0), ProgressBar()],
)
```

### Training Strategies

#### TestConvergence (default)

Each epoch: agent generates/modifies code -> run tests -> measure pass rate -> if not converged, agent sees failures and tries again. Rollback on regression.

```
Epoch 1: Generate scaffold        -> 0/12 tests pass
Epoch 2: Implement core logic     -> 5/12 tests pass
Epoch 3: Fix bugs                 -> 10/12 tests pass
Epoch 4: Fix edge cases           -> 12/12 tests pass -> CONVERGED
```

Early stopping: if no improvement for N epochs, stop and return best checkpoint.
Rollback: if epoch makes things worse, restore previous best and try different approach.

#### Curriculum

Progressive complexity. Each stage adds requirements. Agent only sees current + previous stages.

```
Stage 1: "Implement data models"    -> 3/3 tests
Stage 2: "Add CRUD"                 -> 7/7 tests
Stage 3: "Add validation"           -> 10/10 tests
Stage 4: "Add pagination"           -> 12/12 tests
Stage 5: "Add filtering"            -> 14/14 tests -> CONVERGED
```

Auto-decomposition from Architecture layer dependencies.

#### Ensemble

Multiple agents run in parallel. Best solution wins. Mutate losers with insights from winners. Repeat.

```
Gen 1: Agent A (0.83) | Agent B (0.92) | Agent C (0.75) -> B wins
Gen 2: Agent A' (0.92) | Agent B' (1.00) | Agent C' (0.83) -> B' CONVERGED
```

Costs N* more but explores multiple solution paths.

#### Passthrough

Wrap an existing agent (Aider, Claude Code, etc.) with standardized evaluation. Chimera monitors and evaluates, the backend agent does the work.

### Result

```python
if result.converged:
    result.codebase.export("./release/")
    print(f"Synthesized in {result.iterations} iterations, cost ${result.total_cost:.2f}")
else:
    print(f"Failed: {result.failure_reason}")
    result.best_codebase.export("./partial/")
```

---

## Layer 6: CLI

```bash
chimera synthesize --spec spec.md --output ./myapp
chimera synthesize --spec spec.md --strategy curriculum --agent claude-sonnet
chimera synthesize --resume ./checkpoints/iteration_7
chimera eval ./myapp --tests ./tests/ --metrics coverage,complexity
chimera bench swe-bench-lite --agent my_agent.py --output results/
chimera compare results/agent_a/ results/agent_b/ --format table
chimera agent --tools read,write,edit,bash --model claude-sonnet
chimera inspect ./checkpoints/ --diff 3..5
```

---

## Package Structure

```
chimera/
|-- __init__.py              # Public API
|-- py.typed
|
|-- core/                    # Layers 2+3: Primitives
|   |-- tool.py              # Tool protocol, ToolResult, ToolGroup
|   |-- loop.py              # Loop protocol, ReAct, PlanAndExecute, etc.
|   |-- prompt.py            # Prompt, PromptTemplate
|   |-- context.py           # Context, Message, compression
|   |-- agent.py             # Agent, Pipeline, Ensemble, Supervisor
|   +-- approval.py          # Approval workflows
|
|-- tools/                   # Built-in tools
|   |-- read.py, write.py, edit.py, search.py, bash.py
|   |-- list_files.py, test.py, delegate.py
|
|-- providers/               # Layer 2: LLM backends
|   |-- base.py, anthropic.py, openai.py, google.py, ollama.py, compatible.py
|
|-- env/                     # Layer 1: Environments
|   |-- base.py, local.py, docker.py, git.py
|
|-- training/                # Layer 5: Training
|   |-- spec.py, architecture.py, trainer.py, constraint.py, callbacks.py
|   +-- strategies/
|       |-- convergence.py, curriculum.py, ensemble.py, passthrough.py
|
|-- eval/                    # Layer 4: Evaluation
|   |-- harness.py, metrics.py
|   |-- anti_overfit/        # mutation.py, adversarial.py, crossval.py
|   +-- benchmarks/          # swebench.py, terminal_bench.py, humaneval.py, custom.py
|
|-- cli/                     # Layer 6: CLI
|   |-- main.py, train.py, eval_cmd.py, bench.py, agent_cmd.py
|
+-- _internal/               # Not public API
    |-- loop_detection.py, context_compression.py, streaming.py
```

### Dependencies

```toml
[project]
name = "chimera-ai"
dependencies = []  # Zero required deps

[project.optional-dependencies]
anthropic = ["anthropic>=0.40"]
openai = ["openai>=1.50"]
google = ["google-genai>=1.0"]
docker = ["docker>=7.0"]
all = ["chimera-ai[anthropic,openai,google,docker]"]
```

---

## API Summary

```python
import chimera

# One-liner (end user)
result = chimera.synthesize("Build a REST API for tasks", tests="./tests/")

# Configured (developer)
trainer = chimera.Trainer(
    architecture=chimera.Architecture([...]),
    spec=chimera.Spec("..."),
    agent=chimera.Agent(provider=chimera.Claude()),
    constraints=[chimera.tests_pass, chimera.coverage(0.8)],
    env=chimera.Environment.local("./output"),
)
result = trainer.synthesize(strategy=chimera.TestConvergence())

# Custom agent (framework author)
class MyAgent(chimera.Agent):
    tools = [chimera.tools.read, chimera.tools.edit, MyCustomTool()]
    loop = chimera.loops.ReAct(max_steps=100)

# Benchmark (researcher)
harness = chimera.eval.Harness(chimera.benchmarks.SWEBenchLite())
results = harness.run(agent=MyAgent())
```

---

## Ideas Ported From Existing Projects

| Source | Idea | Where in Chimera |
|--------|------|-----------------|
| NovalisCode | D-Mail checkpointing | Environment.checkpoint/restore |
| NovalisCode | 3-layer loop detection | _internal/loop_detection.py |
| NovalisCode | Smart 3-tier edit | tools/edit.py |
| NovalisCode | Benchmark harness | eval/harness.py |
| NovalisGraph | Node composition (prep/exec/post) | Inspired Layer lifecycle |
| NovalisGraph | Graph routing | Inspired Pipeline/Supervisor |
| Pi | Streaming tool output | Tool.execute yields ToolUpdate |
| Pi | Extension-based architecture | Plugin-friendly Tool/Loop protocols |
| Pi | Session branching | Environment.checkpoint branching |
| OpenCode | Permission system (allow/deny/ask) | core/approval.py |
| OpenCode | DOOM_LOOP detection | _internal/loop_detection.py |
| OpenCode | Multi-agent dispatch | Pipeline, Supervisor, delegate |
| OpenCode | Skill system | Prompt templates |
| SWE-agent | ReAct loop | loops/ReAct |
| SWE-agent | Docker sandbox | env/docker.py |
| SWE-agent | Thought-action separation | Context.messages |
| Aider | Edit format variety | tools/edit.py strategies |
| Aider | Repository mapping | Context.files |
| MetaGPT | Role-based multi-agent | Supervisor pattern |
| ML theory | Anti-overfitting | eval/anti_overfit/ |
| ML theory | Codebase as trained model | Training layer paradigm |
| ML theory | Spec as loss function | training/spec.py |
| Framework design | Layer/Model/compile/fit | Architecture/Trainer/synthesize |
| Framework design | Callbacks | training/callbacks.py |
| Framework design | Zero-dep core | No required dependencies |

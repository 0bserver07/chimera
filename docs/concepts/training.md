# Training

Chimera borrows the language of machine learning to describe code synthesis. A **Spec** is the loss function (what to build), a **Strategy** controls the training loop (how to iterate), **Constraints** act as regularization (guardrails), and the **Trainer** ties everything together. The generated code is the trained model.

## The ML Analogy

| ML Concept | Chimera Equivalent |
|------------|-------------------|
| Loss function | `Spec` -- defines what success looks like |
| Training loop | `Strategy` -- controls iteration, stopping, rollback |
| Regularization | `Constraint` -- enforces quality rules |
| Epoch | One agent run + test cycle |
| Model | The generated code |
| Trainer | `Trainer` -- orchestrates spec + agent + strategy + env |

## Spec

A `Spec` defines what the agent should build. It renders into a prompt string that drives the agent.

### Constructors

```python
from chimera.training.spec import Spec

# From a text description
spec = Spec.from_string("Build a REST API with user authentication")

# From a spec file (reads content from disk)
spec = Spec.from_file("specs/api.md")

# From a test directory (convergence = all tests pass)
spec = Spec.from_tests("tests/", description="Build a calculator module")
```

### Rendering

`to_prompt()` converts the spec into a prompt string:

```python
spec = Spec.from_tests("tests/", description="Build a calculator")
print(spec.to_prompt())
# "Build a calculator\n\nTests directory: tests/"
```

## Architecture

For multi-layer projects, `Architecture` defines layers with dependencies. Each `Layer` is a logical unit of code to synthesize.

```python
from chimera.training.architecture import Architecture, Layer

arch = Architecture(layers=[
    Layer(name="models", description="Data models"),
    Layer(name="db", depends_on=["models"], description="Database layer"),
    Layer(name="api", depends_on=["db", "models"], description="REST API"),
])
```

Each `Layer` has `name`, `depends_on`, `description`, `template`, `frozen`, and `constraints` fields. Call `arch.build_order()` to get layers in topological order (Kahn's algorithm). Circular dependencies are detected at construction time and raise a `ValueError`.

## Trainer

The `Trainer` is the top-level orchestrator. It ties together a Spec, Agent, Environment, and optionally an Architecture and Constraints.

```python
from chimera.training.trainer import Trainer

trainer = Trainer(
    spec=spec,
    agent=agent,
    env=env,
    architecture=arch,        # Optional
    constraints=constraints,  # Optional
)

result = trainer.synthesize(
    strategy=TestConvergence(max_iterations=50, patience=5),
    callbacks=[my_callback],
)
```

`synthesize()` defaults to `TestConvergence` if no strategy is specified.

## Strategies

A `Strategy` controls how the agent iterates toward a solution. All strategies implement the `Strategy` ABC with a single `run(agent, spec, env, constraints, callbacks) -> SynthesisResult` method.

### Built-in Strategies

| Strategy | Description |
|----------|-------------|
| `TestConvergence` | Iterate until all tests pass or patience is exhausted. Rolls back on regression. Default strategy. |
| `TreeSearch` | Best-first tree search over parallel solution branches. Clones the environment and explores multiple approaches concurrently. |
| `CurriculumStrategy` | Process layers in topological order (requires an `Architecture`). Each layer gets its own mini-synthesis. |
| `EnsembleStrategy` | Run N independent attempts from a fresh checkpoint, pick the best result by pass rate. |
| `MajorityVoting` | Sample N solutions, extract answers, pick the consensus via majority vote. Includes early stopping. |
| `AIMOEnsemble` | Two-phase: MajorityVoting first, TreeSearch fallback if no consensus is reached. |
| `Passthrough` | Single-shot: run the agent once, no iteration. |
| `CEGISStrategy` | Counterexample-Guided Inductive Synthesis. Each epoch focuses on the first failing test (the counterexample) rather than showing all failures. Reduces oscillation where fixing one test breaks another. |
| `IncrementalStrategy` | Identifies which functions are covered by failing tests and asks the agent to rewrite only those functions, rather than re-prompting with the whole codebase. |

### Strategy Examples

```python
from chimera.training.strategies import (
    TestConvergence, TreeSearch, MajorityVoting, AIMOEnsemble,
)

# TestConvergence: iterate until tests pass, rollback on regression
strategy = TestConvergence(max_iterations=100, patience=5)

# TreeSearch: parallel branch exploration with cloned environments
strategy = TreeSearch(branch_factor=3, max_depth=5, max_nodes=20, max_cost=10.0)

# MajorityVoting: sample N solutions, pick consensus by vote
strategy = MajorityVoting(n_samples=16, temperature=0.7, min_agreement=2)

# AIMOEnsemble: MajorityVoting first, TreeSearch fallback
strategy = AIMOEnsemble(voting_samples=8, min_agreement=2, tree_branch_factor=3)

# CEGISStrategy: one counterexample at a time
from chimera.training.strategies.cegis import CEGISStrategy
strategy = CEGISStrategy(max_iterations=50, patience=10)

# IncrementalStrategy: re-synthesize only failing functions
from chimera.training.strategies.incremental import IncrementalStrategy
strategy = IncrementalStrategy(max_iterations=20, patience=5)
```

## Constraints

Constraints are guardrails evaluated after each epoch. They support two modes: environment-based evaluation (runs tests, inspects files) and TestResult-based checks.

```python
from chimera.training.constraint import Constraint

# Built-in factory methods
constraints = [
    Constraint.tests_pass(),           # All tests must pass
    Constraint.min_pass_rate(0.8),     # At least 80% pass rate
    Constraint.max_files(10),          # No more than 10 files
    Constraint.max_total_lines(500),   # No more than 500 lines total
    Constraint.no_syntax_errors(),     # No Python syntax errors
    Constraint.no_security_issues(),   # No eval(), exec(), shell=True
    Constraint.max_complexity(15),     # Cyclomatic complexity limit
]

# Custom constraint
Constraint.custom(
    name="no_print",
    fn=lambda env: "print(" not in env.read_file("main.py"),
    message="No print statements allowed",
)
```

## Callbacks and SynthesisResult

Callbacks observe synthesis via the `Callback` ABC. Implement `on_synthesis_start()`, `on_epoch_start(epoch)`, `on_epoch_end(epoch, result)`, and `on_synthesis_end(result)`. Return `False` from `on_epoch_end` to stop early.

`SynthesisResult` is the return value of `trainer.synthesize()`, containing `converged` (bool), `iterations` (int), `total_cost` (float), `best_pass_rate` (float), `history` (list of `EpochResult`), and `failure_reason` (str or None).

## Full Configured Example

```python
from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.factory import create_provider
from chimera.training.constraint import Constraint
from chimera.training.spec import Spec
from chimera.training.strategies import TestConvergence
from chimera.training.trainer import Trainer

# 1. Provider
provider = create_provider(model="claude-sonnet-4-20250514")

# 2. Agent
agent = Agent(
    provider=provider,
    tools=list(DEFAULT_TOOLS),
    loop=ReAct(max_steps=50),
)

# 3. Environment
env = LocalEnvironment(
    workdir="./output",
    test_cmd="python -m pytest tests/ -v",
)
env.setup()

# 4. Spec
spec = Spec.from_tests("tests/", description="Implement a URL shortener service")

# 5. Constraints
constraints = [
    Constraint.max_files(15),
    Constraint.max_total_lines(1000),
]

# 6. Train
trainer = Trainer(spec=spec, agent=agent, env=env, constraints=constraints)
result = trainer.synthesize(
    strategy=TestConvergence(max_iterations=20, patience=3),
)

print(f"Converged: {result.converged}")
print(f"Iterations: {result.iterations}")
print(f"Best pass rate: {result.best_pass_rate:.0%}")
print(f"Total cost: ${result.total_cost:.2f}")
```

## API Reference

- `chimera.training.trainer.Trainer` -- top-level orchestrator
- `chimera.training.spec.Spec` -- synthesis specification
- `chimera.training.architecture.Architecture` -- multi-layer dependency graph
- `chimera.training.architecture.Layer` -- single layer definition
- `chimera.training.constraint.Constraint` -- quality guardrails
- `chimera.training.strategies.base.Strategy` -- strategy ABC
- `chimera.training.strategies.base.Callback` -- lifecycle observer
- `chimera.training.strategies.base.SynthesisResult` -- final result
- `chimera.training.strategies.base.EpochResult` -- per-epoch result

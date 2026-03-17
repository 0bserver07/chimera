# ML & Program Synthesis Primitives for Chimera

**Date:** 2026-03-16
**Status:** Spec
**Motivation:** The observation that agentic coding is machine learning — the spec is the loss function, the agent loop is the optimizer, the codebase is the trained model. This implies all classic ML problems (overfitting, Clever Hans, concept drift) apply. And program synthesis techniques (CEGIS, sketching, oracles) offer proven solutions.



---

## Overview

7 features that bring ML and program synthesis rigor to Chimera's training loop. All plug into existing infrastructure (`Callback`, `Constraint`, `Strategy`, `Spec`, `Critic`, `Harness`) without changing their APIs.

| # | Feature | ML Analogy | Synthesis Analogy | Est. Lines |
|---|---------|-----------|-------------------|------------|
| 1 | Training Curves | Loss curves | — | ~80 |
| 2 | Validation Splits | Train/val split | — | ~100 |
| 3 | Regularization | L1/L2, dropout | — | ~120 |
| 4 | Hyperparameter Search | Grid search, hyperparameter search | — | ~150 |
| 5 | CEGIS Strategy | — | Counterexample-guided | ~100 |
| 6 | Sketch Synthesis | — | Synthesis by sketching | ~80 |
| 7 | Growing Test Suite | — | Oracle-guided synthesis | ~100 |

---

## Feature 1: Training Curves

### Problem
No visibility into how synthesis progresses over epochs. Can't tell if the agent is plateauing, oscillating, or diverging.

### Design

**File:** `chimera/training/callbacks.py` (extend existing)

```python
class TrainingCurveCallback(Callback):
    """Log per-epoch metrics and diagnose training patterns."""

    def __init__(self, output_path: str | None = None) -> None:
        self.epochs: list[EpochResult] = []
        self._output_path = output_path

    def on_epoch_end(self, result: EpochResult) -> None:
        self.epochs.append(result)

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        if self._output_path:
            self._write_json()

    def summary(self) -> str:
        """Text summary with per-epoch pass_rate and cost."""
        lines = []
        for e in self.epochs:
            bar = "#" * int(e.pass_rate * 20)
            lines.append(f"  Epoch {e.epoch:2d}: {e.pass_rate:5.1%} |{bar:<20}| ${e.cost:.4f}")
        return "\n".join(lines)

    def diagnose(self) -> list[str]:
        """Detect patterns: plateau, oscillation, cost explosion, instant convergence."""
        ...

    def to_dict(self) -> list[dict]:
        """Export as JSON-serializable list of epoch data."""
        ...
```

### Diagnostics

| Pattern | Detection | Implication |
|---------|-----------|-------------|
| Plateau | pass_rate unchanged for 3+ epochs | Try different strategy or model |
| Oscillation | pass_rate alternates up/down 4+ epochs | Agent fixing one test, breaking another — needs D-Mail or better context |
| Cost explosion | per-epoch cost increases >2x | Context bloated — needs compaction |
| Instant convergence | pass_rate=1.0 on epoch 1 | Spec too easy, or agent may be cheating |

### Usage

```python
curve = TrainingCurveCallback(output_path="training.json")
result = trainer.synthesize(callbacks=[curve])
print(curve.summary())
for warning in curve.diagnose():
    print(f"  WARNING: {warning}")
```

### Tests
- `test_curve_records_epochs` — epochs list populated after synthesis
- `test_curve_summary_format` — summary string contains pass rates
- `test_curve_diagnose_plateau` — detects 3+ unchanged epochs
- `test_curve_diagnose_oscillation` — detects alternating pass rates
- `test_curve_diagnose_instant` — detects epoch-1 convergence
- `test_curve_to_dict` — JSON export matches epoch data
- `test_curve_output_file` — writes JSON to disk when path given

---

## Feature 2: Validation Splits

### Problem
The agent trains directly against the full test suite. No way to detect overfitting — code that passes visible tests but fails on unseen cases.

### Design

**File:** `chimera/training/validation.py`

```python
@dataclass
class ValidationResult:
    """Result of evaluating against held-out validation tests."""
    train_pass_rate: float
    val_pass_rate: float
    overfit_gap: float      # train_pass_rate - val_pass_rate
    train_passed: int
    val_passed: int
    train_total: int
    val_total: int

class ValidationSplit:
    """Split a test suite into training and validation sets.

    The agent synthesizes against training tests only. Validation tests
    are held out and used for evaluation after synthesis completes.
    """

    def __init__(
        self,
        spec: Spec,
        ratio: float = 0.3,
        seed: int | None = None,
    ) -> None:
        ...

    @property
    def train_spec(self) -> Spec:
        """Spec with only training test files."""

    @property
    def val_spec(self) -> Spec:
        """Spec with only validation test files."""

    def evaluate(self, env: Environment) -> ValidationResult:
        """Run both train and val tests against current env state."""
```

### Splitting strategy

Split by **test file**, not individual test functions. This avoids import/fixture issues. If the test dir has 10 `.py` files, 7 go to train, 3 to val (with ratio=0.3).

Implementation: copy the test directory to two temp directories (train/ and val/), each with their subset of files. `train_spec` points to the train/ copy, `val_spec` to the val/ copy.

### Overfit detection

```
overfit_gap = train_pass_rate - val_pass_rate
```

- `gap < 0.1` — generalized well
- `gap 0.1 - 0.3` — mild overfitting, might still be acceptable
- `gap > 0.3` — significant overfitting, agent is likely exploiting test structure

### Usage

```python
split = ValidationSplit(spec, ratio=0.3, seed=42)
result = trainer.synthesize(spec=split.train_spec)

val = split.evaluate(env)
print(f"Train: {val.train_pass_rate:.0%}, Val: {val.val_pass_rate:.0%}")
print(f"Overfit gap: {val.overfit_gap:.0%}")
if val.overfit_gap > 0.3:
    print("WARNING: significant overfitting detected")
```

### Tests
- `test_split_ratios` — 70/30 split produces correct file counts
- `test_split_deterministic` — same seed produces same split
- `test_train_spec_excludes_val` — no overlap between train and val
- `test_evaluate_returns_both_rates` — both rates computed correctly
- `test_overfit_gap_calculation` — gap = train - val
- `test_split_single_file` — edge case: 1 file goes to train (can't split)

---

## Feature 3: Regularization

### Problem
No mechanism to prefer simpler solutions when multiple solutions pass the tests. Agent might generate unnecessarily complex code.

### Design

Two mechanisms: weighted constraints and critic-as-regularizer.

### A) Weighted constraints

**File:** Modify `chimera/training/constraint.py`

Add `score` field to `ConstraintResult`:

```python
@dataclass
class ConstraintResult:
    name: str
    satisfied: bool
    message: str
    value: Any = None
    score: float = 1.0  # NEW: 0.0 (worst) to 1.0 (best)
```

New penalty-based constraint factories:

```python
@staticmethod
def complexity_penalty(max_complexity: int = 10) -> Constraint:
    """Score decreases as cyclomatic complexity increases beyond threshold."""
    # score = max(0, 1 - (actual_complexity - max_complexity) / max_complexity)
    # Uses ast to compute complexity

@staticmethod
def line_count_penalty(target: int = 200, hard_max: int = 500) -> Constraint:
    """Score = 1.0 at target lines, decreases linearly to 0.0 at hard_max."""

@staticmethod
def duplication_penalty(threshold: float = 0.1) -> Constraint:
    """Penalize files with >threshold fraction of duplicate lines."""
    # Uses difflib.SequenceMatcher across functions
```

### B) Critic-as-regularizer

**File:** `chimera/training/regularization.py`

```python
class RegularizationCallback(Callback):
    """Evaluate code quality after each epoch using a Critic.

    When tests pass, the critic scores the generated code on
    readability, maintainability, and simplicity. The score is
    stored alongside the EpochResult for strategy selection.
    """

    def __init__(
        self,
        critic: Critic,
        weight: float = 0.3,
        min_pass_rate: float = 0.5,
    ) -> None:
        self.critic = critic
        self.weight = weight
        self.min_pass_rate = min_pass_rate
        self.scores: list[CriticResult] = []

    def on_epoch_end(self, result: EpochResult) -> None:
        if result.pass_rate >= self.min_pass_rate:
            critic_result = self._evaluate(result)
            self.scores.append(critic_result)

    def combined_score(self, epoch_idx: int) -> float:
        """pass_rate * (1 - weight) + critic_score * weight"""
```

### Usage

```python
# Weighted constraints
constraints = [
    Constraint.tests_pass(),
    Constraint.complexity_penalty(max_complexity=8),
    Constraint.line_count_penalty(target=100),
]

# Critic regularization
from chimera.critic import ChecklistCritic
critic = ChecklistCritic(
    checklist=["No hardcoded values", "Functions under 20 lines", "No global state"],
    provider=provider,
)
reg = RegularizationCallback(critic=critic, weight=0.3)
result = trainer.synthesize(callbacks=[reg])
```

### Tests
- `test_constraint_score_field` — ConstraintResult has score, defaults to 1.0
- `test_complexity_penalty_scores` — low complexity → high score, high → low
- `test_line_count_penalty` — at target → 1.0, at hard_max → 0.0
- `test_duplication_penalty` — duplicate code → lower score
- `test_reg_callback_skips_low_pass_rate` — doesn't evaluate failing code
- `test_reg_callback_combined_score` — weighted combination is correct

---

## Feature 4: Hyperparameter Search

### Problem
No automated way to find the best configuration (model, strategy, temperature, prompt) for a given spec. Users manually try different settings.

### Design

**File:** `chimera/training/tuner.py`

```python
class SearchSpace:
    """Define the hyperparameter search space."""

    def __init__(self) -> None:
        self._params: dict[str, list] = {}

    def choice(self, name: str, values: list) -> SearchSpace:
        """Add a categorical parameter."""
        self._params[name] = values
        return self

    def configurations(self) -> list[dict]:
        """Generate all combinations (grid search)."""
        # itertools.product over all param values
        ...

@dataclass
class TrialResult:
    config: dict
    synthesis_result: SynthesisResult
    score: float  # primary metric value

@dataclass
class TunerResult:
    best_config: dict
    best_score: float
    trials: list[TrialResult]
    total_cost: float

class SynthesisTuner:
    """Grid search over synthesis configurations.

    Creates a fresh environment for each trial, runs synthesis,
    and selects the best configuration by the chosen metric.
    """

    def __init__(
        self,
        spec: Spec,
        env_factory: Callable[[], Environment],
        agent_factory: Callable[[dict], Agent] | None = None,
    ) -> None:
        ...

    def search(
        self,
        space: SearchSpace,
        max_trials: int | None = None,
        metric: str = "pass_rate",
        callbacks: list[Callback] | None = None,
    ) -> TunerResult:
        """Run synthesis for each configuration, return best."""
        ...
```

### Supported hyperparameters

The `agent_factory` receives the config dict and builds an Agent accordingly:

```python
def my_agent_factory(config: dict) -> Agent:
    provider = create_provider(model=config.get("model", "glm-5"))
    strategy_cls = {"convergence": TestConvergence, "tree_search": TreeSearch}[config["strategy"]]
    return Agent(provider=provider, tools=list(AGENT_TOOLS),
                 loop=ReAct(max_steps=config.get("max_steps", 25)))
```

If `agent_factory` is None, the tuner uses a default factory that reads `model` and `max_steps` from the config.

### Usage

```python
space = SearchSpace()
space.choice("model", ["glm-5", "claude-sonnet-4-20250514"])
space.choice("strategy", ["convergence", "tree_search"])
space.choice("max_steps", [10, 25])

tuner = SynthesisTuner(
    spec=spec,
    env_factory=lambda: LocalEnvironment(workdir=tempfile.mkdtemp()),
)
result = tuner.search(space, max_trials=6)
print(f"Best: {result.best_config}")
print(f"Score: {result.best_score:.0%}")
print(f"Cost: ${result.total_cost:.4f}")
```

### Tests
- `test_search_space_combinations` — 2x2 grid produces 4 configs
- `test_search_space_single` — 1 param produces N configs
- `test_tuner_picks_best` — highest pass_rate config selected
- `test_tuner_max_trials` — limits number of trials
- `test_tuner_total_cost` — sums all trial costs
- `test_tuner_custom_metric` — can sort by cost instead of pass_rate

---

## Feature 5: CEGIS Strategy

### Problem
`TestConvergence` shows all failures at once. Agents often get confused fixing multiple issues simultaneously — fix one, break another (oscillation).

### Design

**File:** `chimera/training/strategies/cegis.py`

Based on Counterexample-Guided Inductive Synthesis. Each epoch focuses on a single failing test (the counterexample). The agent fixes that one test, then we find the next counterexample.

```python
class CEGISStrategy(Strategy):
    """Counterexample-Guided Inductive Synthesis.

    Each epoch:
    1. Run all tests
    2. If all pass → converged
    3. Pick the FIRST failing test as the counterexample
    4. Prompt the agent with ONLY that failure
    5. Agent fixes it
    6. Repeat

    This focuses the agent on one problem at a time, reducing
    oscillation where fixing one test breaks another.
    """

    def __init__(
        self,
        max_iterations: int = 50,
        patience: int = 10,
    ) -> None:
        self._max_iterations = max_iterations
        self._patience = patience

    def run(self, agent, spec, env, constraints=None, callbacks=None) -> SynthesisResult:
        ...

    def _extract_first_failure(self, test_result: TestResult) -> str:
        """Parse test output to find the first failing test name + traceback."""
        ...

    def _build_cegis_prompt(self, spec: Spec, failure: str, history: list[str]) -> str:
        """Build a prompt focused on a single counterexample.

        Includes:
        - Original spec
        - The ONE failing test (name + assertion error + traceback)
        - History of previously fixed counterexamples
        - Instruction: fix THIS test without breaking previously fixed ones
        """
        ...
```

### Key difference from TestConvergence

| Aspect | TestConvergence | CEGISStrategy |
|--------|----------------|---------------|
| Failures shown | All at once | One at a time |
| Prompt focus | "Fix all these" | "Fix this ONE test" |
| History | Previous agent output | List of fixed counterexamples |
| Best for | Small test suites (<10) | Large test suites (10+) |
| Oscillation risk | High | Low |

### Usage

```python
from chimera.training.strategies.cegis import CEGISStrategy

strategy = CEGISStrategy(max_iterations=30, patience=8)
result = trainer.synthesize(strategy=strategy)
```

### Tests
- `test_cegis_converges` — fixes tests one at a time until all pass
- `test_cegis_single_failure_prompt` — prompt contains only one test name
- `test_cegis_history_grows` — fixed counterexamples accumulate
- `test_cegis_patience` — stops after N epochs without progress
- `test_cegis_no_oscillation` — previously fixed tests stay fixed (mock)

---

## Feature 6: Sketch Synthesis

### Problem
Agent rewrites entire files from scratch each epoch. This is wasteful when the user already knows the structure and just needs specific logic filled in.

### Design

**File:** `chimera/training/sketch.py`

A sketch is a source file with `# HOLE: description` markers. The agent fills only the holes, preserving the surrounding code.

```python
@dataclass
class Hole:
    """A hole in a sketch that the agent must fill."""
    id: int
    description: str
    line_start: int
    line_end: int
    indent: str  # indentation level of the hole marker

class SketchSpec(Spec):
    """Spec created from source files with HOLE markers.

    Parses files for '# HOLE: <description>' comments. The agent
    receives the full file context but is instructed to only fill
    the marked holes.
    """

    def __init__(self, files: dict[str, str], description: str | None = None) -> None:
        self._files = files  # path -> content with HOLEs
        self._holes: dict[str, list[Hole]] = {}
        self._parse_holes()

    @classmethod
    def from_file(cls, path: str) -> SketchSpec:
        """Load a single sketch file."""

    @classmethod
    def from_directory(cls, path: str, pattern: str = "**/*.py") -> SketchSpec:
        """Load all sketch files matching pattern."""

    def to_prompt(self) -> str:
        """Generate a prompt showing the sketch with numbered holes.

        The prompt instructs the agent:
        - Read the existing code structure
        - Fill ONLY the marked holes
        - Do not modify code outside the holes
        - Respond with the filled code for each hole
        """

    @property
    def holes(self) -> list[Hole]:
        """All holes across all files."""

    def apply_fills(self, fills: dict[int, str], env: Environment) -> None:
        """Write the filled sketch to the environment."""
```

### Sketch file format

```python
# calculator.py — sketch

def add(a: float, b: float) -> float:
    # HOLE: implement addition
    pass

def divide(a: float, b: float) -> float:
    # HOLE: implement division, raise ValueError on zero
    pass
```

### Usage

```python
sketch = SketchSpec.from_file("calculator.py")
print(f"Found {len(sketch.holes)} holes to fill")

# Use with regular trainer
result = trainer.synthesize(spec=sketch)
```

### Tests
- `test_parse_holes` — finds HOLE markers with correct line numbers
- `test_from_file` — loads sketch from a file
- `test_to_prompt` — prompt lists holes with descriptions
- `test_apply_fills` — writes filled code to env
- `test_preserves_surrounding_code` — code outside holes unchanged
- `test_multiple_files` — handles sketches across multiple files

---

## Feature 7: Growing Test Suite (Oracle-Guided)

### Problem
Fixed test suites enable overfitting. The agent can learn to exploit the specific test structure rather than implementing the general solution.

### Design

**File:** `chimera/training/oracle.py`

After each epoch, generate new test cases that probe edge cases the agent might be cheating on. The test suite grows during synthesis.

```python
class OracleCallback(Callback):
    """Grow the test suite during synthesis.

    After each epoch where tests pass, generates new test cases
    targeting edge cases and boundary conditions. The agent must
    pass BOTH the original and new tests in subsequent epochs.

    Two modes:
    - LLM oracle: uses a provider to generate adversarial tests
    - Property oracle: uses property-based testing (hypothesis-style)
    """

    def __init__(
        self,
        provider: Provider | None = None,
        tests_dir: str | None = None,
        max_new_tests_per_epoch: int = 3,
        mode: Literal["llm", "property"] = "llm",
    ) -> None:
        self._provider = provider
        self._tests_dir = tests_dir
        self._max_new = max_new_tests_per_epoch
        self._mode = mode
        self.generated_tests: list[str] = []

    def on_epoch_end(self, result: EpochResult) -> None:
        if result.pass_rate == 1.0 and self._tests_dir:
            new_tests = self._generate_tests(result)
            self._write_tests(new_tests)
            self.generated_tests.extend(new_tests)

    def _generate_tests_llm(self, result: EpochResult) -> list[str]:
        """Use LLM to generate adversarial test cases.

        Prompt: "Here is the implementation. Write 3 edge-case tests
        that might expose bugs. Focus on boundary conditions, empty
        inputs, negative numbers, unicode, large inputs, etc."
        """

    def _generate_tests_property(self, result: EpochResult) -> list[str]:
        """Generate property-based test stubs.

        Analyzes function signatures in the implementation and generates
        hypothesis-style tests for common properties:
        - Commutativity: f(a,b) == f(b,a)
        - Identity: f(a, identity) == a
        - Idempotence: f(f(a)) == f(a)
        - Inverse: g(f(a)) == a
        """
```

### Test generation prompt (LLM mode)

```
Here is the current implementation:

{generated_code}

It passes all current tests. Write {n} new test functions that target
edge cases the implementation might fail on. Focus on:
- Boundary conditions (0, -1, empty, None)
- Large inputs
- Unicode/special characters
- Type edge cases
- Concurrent/repeated calls

Output each test as a standalone function starting with test_.
```

### Usage

```python
oracle = OracleCallback(
    provider=provider,
    tests_dir="./tests/",
    max_new_tests_per_epoch=3,
    mode="llm",
)
result = trainer.synthesize(callbacks=[oracle])
print(f"Generated {len(oracle.generated_tests)} new tests during synthesis")
```

### Tests
- `test_oracle_generates_on_full_pass` — generates tests when pass_rate=1.0
- `test_oracle_skips_on_failure` — no new tests when tests are failing
- `test_oracle_writes_to_dir` — new tests appear as files in tests_dir
- `test_oracle_max_per_epoch` — respects max_new_tests_per_epoch limit
- `test_oracle_accumulates` — generated_tests list grows across epochs

---

## Dependencies Between Features

```
1. Training Curves     — independent, no deps
2. Validation Splits   — independent
3. Regularization      — independent (optionally uses Critic)
4. Hyperparameter Search — independent (uses Trainer internally)
5. CEGIS Strategy      — independent
6. Sketch Synthesis    — independent
7. Growing Test Suite  — independent (optionally uses Provider for LLM mode)
```

All 7 are fully independent. Any subset can be implemented without the others.

## Implementation Order (recommended)

```
1. Training Curves        — simplest, pure Callback, immediate value
5. CEGIS Strategy         — standalone Strategy, direct comparison with TestConvergence
2. Validation Splits      — Spec manipulation, clear success metric
6. Sketch Synthesis       — Spec subclass, file parsing
3. Regularization         — extends Constraint + new Callback
7. Growing Test Suite     — most complex Callback (needs LLM or codegen)
4. Hyperparameter Search  — orchestration layer, benefits from the others existing
```

## Exports

Add to `chimera/__init__.py`:
```python
from chimera.training.callbacks import TrainingCurveCallback
from chimera.training.validation import ValidationSplit, ValidationResult
from chimera.training.regularization import RegularizationCallback
from chimera.training.tuner import SynthesisTuner, SearchSpace, TunerResult
from chimera.training.strategies.cegis import CEGISStrategy
from chimera.training.sketch import SketchSpec, Hole
from chimera.training.oracle import OracleCallback
```

## Test Files

```
tests/test_training_curve.py     — 7 tests
tests/test_validation_split.py   — 6 tests
tests/test_regularization.py     — 6 tests
tests/test_tuner.py              — 6 tests
tests/test_cegis.py              — 5 tests
tests/test_sketch.py             — 6 tests
tests/test_oracle.py             — 5 tests
                                   -------
                                   41 tests total
```

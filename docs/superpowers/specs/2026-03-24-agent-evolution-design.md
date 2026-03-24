# Agent Evolution — Design Spec

**Date:** 2026-03-24
**Status:** Approved
**Scope:** 4 subsystems, 8 modules, ~20 new files

## Overview

Chimera's agents run, produce results, and forget. Each session starts from zero. This spec adds four subsystems that make agents learn, route intelligently, maintain discipline, and evaluate rigorously — the operational layer that turns Chimera from a stateless framework into an adaptive one.

These subsystems are grounded in Chimera's ML framing: adaptive learning is online gradient descent, smart dispatch is an attention mechanism, workflow discipline is regularization, and pluggable evaluation is the loss function getting sharper.

### Subsystem Map

| Subsystem | New Module | What It Does | ML Analogy |
|-----------|-----------|--------------|------------|
| Adaptive Learning | `chimera/learning/` | Record error-fix patterns, track confidence, inject proven fixes | Online learning with feedback |
| Smart Dispatch | `chimera/agents/dispatch/` | Classify requests, route to best agent automatically | Mixture of experts / attention |
| Workflow Discipline | `chimera/discipline/` | Phase gates, scope guards, instruction anchoring | Regularization, early stopping |
| Review & Eval | `chimera/review/` + `chimera/eval/graders/` | Pluggable review perspectives, composable graders | Better loss functions |

### Connections to Synthesis Specs

These subsystems provide the operational foundation for four synthesis proposals already in `docs/specs/`:

- **Adaptive Learning** feeds into **Neural-Guided Search** (`docs/specs/neural-guided-search.md`) — synthesis traces become training data for learned search policies
- **Smart Dispatch** pairs with **DSL Grammars** (`docs/specs/formal-dsl-grammar.md`) — the dispatcher can use grammar constraints to narrow which agents are valid for a request
- **Workflow Discipline** enforces **Formal Verification** (`docs/specs/formal-verification-integration.md`) — phase gates can require Z3/Lean proofs before advancing
- **Pluggable Graders** enable **Programming by Example** (`docs/specs/programming-by-example.md`) — I/O example grading becomes a first-class eval strategy

---

## Subsystem 1: Adaptive Learning

### Problem

Chimera agents encounter errors, fix them, and move on. The fix is lost when the session ends. The same error in the next session triggers the same investigation from scratch. There's no mechanism for agents to accumulate operational knowledge.

### What This Adds

A persistent learning system that records error-fix patterns with confidence scores, automatically tracks whether fixes actually work, and injects proven solutions into future agent runs. Agents get measurably better over time.

### Module: `chimera/learning/`

#### `observation.py` — What gets recorded

```python
class ObservationCategory(Enum):
    ERROR = "error"          # Tool errors, exceptions, test failures
    DEBUG = "debug"          # Root cause findings
    DESIGN = "design"        # Architectural decisions
    REVIEW = "review"        # Code review findings
    EFFECTIVENESS = "effectiveness"  # What worked, what didn't

# Confidence thresholds for injection (higher = more conservative)
CATEGORY_THRESHOLDS: dict[ObservationCategory, float] = {
    ObservationCategory.ERROR: 0.50,
    ObservationCategory.DEBUG: 0.60,
    ObservationCategory.DESIGN: 0.70,
    ObservationCategory.REVIEW: 0.70,
    ObservationCategory.EFFECTIVENESS: 0.50,
}

@dataclass
class Observation:
    topic: str                          # What area (e.g., "pytest", "import resolution")
    key: str                            # Specific pattern (e.g., "ModuleNotFoundError for relative imports")
    value: str                          # What to do about it (the fix or insight)
    category: ObservationCategory
    confidence: float = 0.5             # 0.0 = unverified, 1.0 = proven
    tags: list[str] = field(default_factory=list)
    source: str = ""                    # Which agent/tool produced this
    project_path: str = ""              # Scoped to project by default
    error_signature: str = ""           # MD5 of normalized error for dedup
    observation_count: int = 1          # Times this pattern was seen
    success_count: int = 0              # Times the fix worked
    failure_count: int = 0              # Times the fix didn't work
```

#### `store.py` — Where it's stored

```python
class LearningStore:
    """SQLite-backed observation store with full-text search.

    Uses WAL mode for concurrent reads during agent execution.
    FTS5 index enables semantic matching against error messages.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        """Default: ~/.chimera/learning/observations.db"""

    def record(self, observation: Observation) -> None:
        """Insert or update an observation. Deduplicates by error_signature."""

    def query(
        self,
        text: str,
        *,
        category: ObservationCategory | None = None,
        project_path: str | None = None,
        min_confidence: float | None = None,
        limit: int = 5,
    ) -> list[Observation]:
        """Full-text search over observations. Returns ranked by relevance * confidence."""

    def update_confidence(
        self,
        observation_id: int,
        success: bool,
    ) -> float:
        """Adjust confidence: success +0.10, failure -0.15 (asymmetric).
        Returns new confidence. Clamps to [0.0, 1.0]."""

    def prune(self, max_age_days: int = 90, min_confidence: float = 0.1) -> int:
        """Remove stale, low-confidence observations. Returns count removed."""
```

Schema (SQLite):
```sql
CREATE TABLE observations (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    tags TEXT DEFAULT '[]',
    source TEXT DEFAULT '',
    project_path TEXT DEFAULT '',
    error_signature TEXT DEFAULT '',
    observation_count INTEGER DEFAULT 1,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(error_signature) ON CONFLICT REPLACE
);

CREATE VIRTUAL TABLE observations_fts USING fts5(
    topic, key, value, tags, content=observations, content_rowid=id
);
```

#### `feedback.py` — Automatic outcome tracking

```python
class FeedbackTracker:
    """Tracks whether error fixes actually work.

    After a ToolResultEvent with an error, opens a feedback window.
    If the same error signature doesn't appear in the next N tool results,
    the fix is considered successful. Otherwise, failure.

    Subscribes to EventBus: ToolResultEvent.
    """

    def __init__(
        self,
        store: LearningStore,
        window_size: int = 3,          # Number of tool calls to wait
    ) -> None: ...

    def on_tool_result(self, event: ToolResultEvent) -> None:
        """Called by EventBus. Detects errors, manages pending feedback."""
```

Flow:
1. Tool result contains error → extract signature (MD5 of normalized message)
2. Query LearningStore for matching observation
3. If match with confidence >= threshold → emit fix suggestion (via steering message)
4. Open pending feedback window (next N tool calls)
5. If error disappears → `store.update_confidence(id, success=True)`
6. If error persists → `store.update_confidence(id, success=False)`
7. If no prior match → `store.record(new_observation)` with confidence 0.5

#### `injector.py` — Context injection

```python
class LearningInjector:
    """Injects relevant learned patterns into agent context.

    Before each turn, queries LearningStore for patterns matching
    the current task/error context. High-confidence matches get
    injected as system context. Conservative — only injects when
    there's a strong, relevant match.
    """

    def __init__(
        self,
        store: LearningStore,
        max_injections: int = 3,
    ) -> None: ...

    def get_injections(
        self,
        context: list[Message],
        project_path: str = "",
    ) -> list[str]:
        """Return formatted injection strings for relevant observations."""
```

#### `metrics.py` — Session-level aggregation

```python
@dataclass
class SessionMetrics:
    """Aggregate metrics for a single session."""
    session_id: str
    start_time: str
    tool_calls: int = 0
    files_modified: int = 0
    errors_encountered: int = 0
    errors_resolved: int = 0
    observations_recorded: int = 0
    total_cost: float = 0.0

class MetricsCollector:
    """Subscribes to EventBus, aggregates session metrics.

    Emits SessionMetricsEvent on session end.
    """

    def __init__(self, store: LearningStore | None = None) -> None: ...
```

#### Integration

```python
# In LoopConfig
@dataclass
class LoopConfig:
    ...
    learning: LearningStore | None = None
    feedback_tracker: FeedbackTracker | None = None
    learning_injector: LearningInjector | None = None
```

---

## Subsystem 2: Smart Dispatch

### Problem

Chimera has agent presets (Build, Plan, Explore, General, Review) and an agent registry, but selecting the right agent is manual. The caller must know which preset fits. For interactive use (CLI, sessions), the system should classify the request and route automatically.

### What This Adds

A dispatch layer that classifies request complexity, matches against agent capabilities via trigger patterns, and returns a configured agent — all without an LLM call. Optional LLM-assisted ranking for ambiguous cases.

### Module: `chimera/agents/dispatch/`

#### `classifier.py` — Request complexity

```python
class Complexity(Enum):
    TRIVIAL = "trivial"      # Quick answer, no code changes
    SIMPLE = "simple"        # Single file, single concern
    MODERATE = "moderate"    # Multi-file, one subsystem
    COMPLEX = "complex"      # Multi-system, needs planning

# Signals that indicate higher complexity
COMPLEX_SIGNALS = frozenset({
    "implement", "create", "build", "refactor", "review", "debug",
    "migrate", "redesign", "architect", "integrate",
})

MULTI_STEP_SIGNALS = frozenset({
    "and also", "then", "first", "after that", "finally",
    "step 1", "step 2", "both", "across",
})

class RequestClassifier:
    """Classify request complexity using heuristics.

    No LLM call. Pure word count + signal detection.
    Deterministic and testable.
    """

    def classify(self, request: str) -> Complexity: ...
        # TRIVIAL: < 10 words and ends with ?
        # SIMPLE: 0-1 complex signals, < 30 words
        # MODERATE: 1-2 complex signals or multi-step signals
        # COMPLEX: 2+ complex signals or 50+ words with multi-step
```

#### `router.py` — Agent selection

```python
@dataclass
class RouteResult:
    agent_config: AgentConfig
    score: float            # 0.0 - 1.0
    reason: str             # Why this agent was selected
    complexity: Complexity

class AgentRouter:
    """Route requests to agents using trigger matching.

    Scoring: trigger keyword matches / total triggers.
    Force-routes override scoring completely.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        force_routes: list[ForceRoute] | None = None,
        index: AgentIndex | None = None,
    ) -> None: ...

    def route(self, request: str) -> list[RouteResult]:
        """Return ranked list of matching agents.
        Force-routes first, then scored matches."""
```

#### `rules.py` — Non-negotiable routing

```python
@dataclass
class ForceRoute:
    """A deterministic routing rule that overrides scoring.

    When the pattern matches, the specified agent is selected
    unconditionally. Used for domain-specific hard rules.
    """
    pattern: str            # Regex pattern to match against request
    agent_name: str         # Agent to force-select
    reason: str             # Why this route is forced

    def matches(self, request: str) -> bool: ...
```

#### `index.py` — Fast agent lookup

```python
class AgentIndex:
    """Pre-computed index of agents for fast routing.

    Scans agent configs and builds a keyword → agent mapping.
    Regenerates when source directory hash changes.
    """

    def __init__(self, registry: AgentRegistry) -> None: ...

    def build(self) -> None:
        """Scan registry and build inverted index."""

    def lookup(self, keywords: list[str]) -> list[tuple[str, float]]:
        """Return (agent_name, relevance_score) pairs."""

    def save(self, path: Path) -> None:
        """Persist index as JSON for fast startup."""

    @classmethod
    def load(cls, path: Path, registry: AgentRegistry) -> AgentIndex: ...
```

#### `dispatcher.py` — Facade

```python
class Dispatcher:
    """Classify, route, and configure agents in one call.

    Usage:
        dispatcher = Dispatcher(registry)
        agent = dispatcher.dispatch("debug this test failure", provider)
        result = agent.run(task, env)
    """

    def __init__(
        self,
        registry: AgentRegistry,
        force_routes: list[ForceRoute] | None = None,
        learning_store: LearningStore | None = None,
    ) -> None: ...

    def dispatch(
        self,
        request: str,
        provider: Provider,
        **agent_kwargs: Any,
    ) -> Agent:
        """Classify → route → configure → return ready Agent."""

    def explain(self, request: str) -> str:
        """Return human-readable routing explanation without executing."""
```

---

## Subsystem 3: Workflow Discipline

### Problem

Agents drift. They over-engineer simple tasks, skip verification steps, make unrelated changes "while they're in there," and forget earlier instructions as context grows. Without structural constraints, agents optimize for apparent progress rather than actual correctness.

### What This Adds

Guards and phase gates that constrain agent behavior — like regularization prevents overfitting. Composable, configurable, and advisory by default (strict when needed).

### Module: `chimera/discipline/`

#### `phase.py` — Structured workflow execution

```python
@dataclass
class Gate:
    """A verifiable condition that must pass before the next phase begins."""
    name: str
    check: Callable[..., bool]     # Returns True if gate passes
    description: str = ""

@dataclass
class Phase:
    """A numbered workflow phase with a goal, steps, and completion gate."""
    number: int
    name: str
    goal: str                       # One sentence: what this phase achieves
    steps: list[str]                # Ordered concrete actions
    gate: Gate                      # Must pass to advance
    read_only: bool = False         # If True, no write/edit tools allowed

class PhasedWorkflow:
    """Execute a workflow as ordered phases with gate enforcement.

    If a gate fails, the phase repeats (up to max_retries).
    Emits PhaseEvent on the EventBus for observability.
    """

    def __init__(
        self,
        phases: list[Phase],
        max_retries: int = 2,
    ) -> None: ...

    def run(self, agent: Agent, task: str, env: Environment) -> AgentResult:
        """Execute phases sequentially. Returns combined result."""

    def current_phase(self) -> Phase | None: ...
```

#### `guard.py` — Behavioral constraints

```python
@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    severity: str = "warning"    # "warning" or "block"

class DisciplineGuard(ABC):
    """Check agent behavior against a constraint.

    Advisory by default (logs warning). When severity="block",
    raises DisciplineViolation.
    """

    @abstractmethod
    def check(self, action: str, context: dict[str, Any]) -> GuardResult: ...

class ScopeGuard(DisciplineGuard):
    """Detect changes unrelated to the stated task.

    Compares modified files against the files mentioned in the task
    description or explored during planning. Flags files that appear
    out of nowhere.
    """
    def __init__(self, task_files: set[str] | None = None) -> None: ...

class DepthGuard(DisciplineGuard):
    """Bound exploration depth to prevent rabbit holes.

    Counts consecutive read/search operations without a write.
    After max_depth, suggests the agent commit to an approach.
    """
    def __init__(self, max_depth: int = 10) -> None: ...

class VerificationGuard(DisciplineGuard):
    """Require test execution before declaring completion.

    Checks that tests were run (ToolCallEvent with tool="test" or "bash"
    containing pytest/test command) before any "done" signal.
    """

class RetryBudgetGuard(DisciplineGuard):
    """Limit retry attempts on the same approach.

    After N failed attempts at the same fix (detected by similar
    edit patterns), forces the agent to try a fundamentally different
    approach or escalate.
    """
    def __init__(self, max_retries: int = 3) -> None: ...
```

#### `anchor.py` — Instruction persistence

```python
class InstructionAnchor:
    """Re-inject key instructions into context periodically.

    Long sessions cause context drift — earlier instructions get
    summarized away or buried. The anchor re-injects critical
    instructions every N turns as a system-level message.

    Compaction-aware: checks if instructions are still present
    before injecting (avoids duplicates).
    """

    def __init__(
        self,
        instructions: list[str],
        interval: int = 10,              # Re-inject every N turns
    ) -> None: ...

    def should_inject(self, turn_count: int, context: list[Message]) -> bool:
        """True if instructions need re-injection."""

    def get_injection(self) -> str:
        """Return formatted instruction text."""
```

#### `patterns.py` — Reusable configurations

```python
# Pre-built discipline configurations

SCOPE_ONLY = [ScopeGuard()]
VERIFY_FIRST = [VerificationGuard()]
PLAN_BEFORE_ACT = [Phase(1, "Plan", "Understand before changing", [...], Gate("explored", ...), read_only=True)]
BOUNDED_RETRY = [RetryBudgetGuard(max_retries=3)]
BOUNDED_EXPLORATION = [DepthGuard(max_depth=10)]

# Compose freely:
STRICT = SCOPE_ONLY + VERIFY_FIRST + BOUNDED_RETRY
```

#### Integration

```python
@dataclass
class LoopConfig:
    ...
    discipline: list[DisciplineGuard] | None = None
    instruction_anchor: InstructionAnchor | None = None
```

Guards are checked in `tool_executor.py` before each tool call (same pattern as permissions and detection).

---

## Subsystem 4: Review & Eval Enhancement

### 4a: Pluggable Review Perspectives

#### Problem

ReviewOrchestrator has 4 hardcoded perspectives (logic, security, tests, architecture). Adding "concurrency" or "performance" requires modifying the orchestrator. Different projects need different review angles.

#### What This Adds

A perspective registry with narrow, focused reviewer definitions. The orchestrator pulls from the registry instead of hardcoding.

#### Changes to `chimera/review/`

```python
# review/perspective.py — NEW

@dataclass
class ReviewPerspective:
    """A focused review lens with its own prompt template."""
    name: str                          # e.g., "concurrency"
    focus_area: str                    # One-line description
    prompt_template: str               # Prompt for the reviewer
    severity_weights: dict[str, float] = field(default_factory=dict)
    languages: list[str] | None = None  # Restrict to specific languages

# Built-in perspectives
BUILTIN_PERSPECTIVES = {
    "logic": ReviewPerspective(
        name="logic",
        focus_area="Correctness: off-by-one, null handling, error paths, return types",
        prompt_template="...",
    ),
    "security": ReviewPerspective(...),
    "tests": ReviewPerspective(...),
    "architecture": ReviewPerspective(...),
    "concurrency": ReviewPerspective(
        name="concurrency",
        focus_area="Race conditions, deadlocks, shared mutable state, atomic operations",
        prompt_template="...",
    ),
    "performance": ReviewPerspective(
        name="performance",
        focus_area="Algorithmic complexity, unnecessary allocations, N+1 queries, caching opportunities",
        prompt_template="...",
    ),
    "type_safety": ReviewPerspective(
        name="type_safety",
        focus_area="Type narrowing, Any escape hatches, missing annotations, generic constraints",
        prompt_template="...",
    ),
    "error_handling": ReviewPerspective(
        name="error_handling",
        focus_area="Exception granularity, error propagation, recovery paths, user-facing messages",
        prompt_template="...",
    ),
}


# review/registry.py — NEW

class PerspectiveRegistry:
    """Register and retrieve review perspectives."""

    def __init__(self) -> None:
        self._perspectives: dict[str, ReviewPerspective] = dict(BUILTIN_PERSPECTIVES)

    def register(self, perspective: ReviewPerspective) -> None: ...
    def get(self, name: str) -> ReviewPerspective: ...
    def list(self) -> list[str]: ...
    def for_language(self, language: str) -> list[ReviewPerspective]: ...


# review/orchestrator.py — MODIFY

class ReviewOrchestrator:
    def __init__(
        self,
        ...,
        perspectives: list[str] | None = None,   # NEW — defaults to ["logic", "security", "tests", "architecture"]
        registry: PerspectiveRegistry | None = None,  # NEW
    ): ...
```

### 4b: Grader Framework

#### Problem

Chimera's eval harness uses pass@k as the primary metric, with benchmarks implementing their own scoring. There's no pluggable way to define custom grading strategies — "did the output create the right file?", "does it match this regex?", "does an LLM judge rate it above 0.7?"

#### What This Adds

A grader ABC with built-in implementations and composition. Eval tasks declare which graders to use. Custom graders are just a subclass away.

#### New module: `chimera/eval/graders/`

```python
# graders/base.py

@dataclass
class GradeResult:
    passed: bool
    score: float              # 0.0 - 1.0
    reason: str = ""
    grader_name: str = ""

class Grader(ABC):
    """Grade an eval task result."""
    name: str

    @abstractmethod
    def grade(self, task: dict, result: dict) -> GradeResult: ...


# graders/builtin.py

class FileExistsGrader(Grader):
    """Check that specified files were created."""
    name = "file_exists"
    def __init__(self, paths: list[str]) -> None: ...

class PatternMatchGrader(Grader):
    """Check that output matches a regex pattern."""
    name = "pattern_match"
    def __init__(self, pattern: str, target: str = "output") -> None: ...

class TestPassGrader(Grader):
    """Run a command and check exit code 0."""
    name = "test_pass"
    def __init__(self, command: str, timeout: int = 60) -> None: ...

class SchemaGrader(Grader):
    """Validate output against a JSON/YAML schema."""
    name = "schema"
    def __init__(self, schema: dict) -> None: ...

class CompositeGrader(Grader):
    """Combine multiple graders with AND/OR logic."""
    name = "composite"
    def __init__(self, graders: list[Grader], mode: str = "all") -> None: ...
    # mode="all": all must pass (AND). mode="any": at least one (OR).


# graders/llm.py

class LLMRubricGrader(Grader):
    """Use an LLM to grade output against a rubric.

    Sends the task output + rubric to a provider and parses
    the structured score. Expensive but catches nuance.
    """
    name = "llm_rubric"
    def __init__(self, provider: Provider, rubric: str) -> None: ...
```

#### Eval task integration

```python
# In chimera/eval/harness.py — add grader support

@dataclass
class EvalTask:
    ...
    graders: list[dict] | None = None   # NEW — [{"type": "test_pass", "command": "pytest"}]
```

---

## Testing Strategy

Each subsystem has its own test file. All use mocks — no real LLM calls or file system side effects.

| Subsystem | Test File | Key Tests |
|-----------|-----------|-----------|
| Learning | `tests/test_learning.py` | Store CRUD, FTS5 search, confidence adjustment (asymmetric), feedback window, dedup by signature, prune, project scoping |
| Dispatch | `tests/test_dispatch.py` | Classifier (trivial/simple/moderate/complex), router scoring, force-route override, index build/save/load, dispatcher facade |
| Discipline | `tests/test_discipline.py` | Phase execution with gates, gate retry, ScopeGuard detection, DepthGuard limit, VerificationGuard check, RetryBudgetGuard, InstructionAnchor interval + compaction-awareness, pattern composition |
| Review | `tests/test_review_perspectives.py` | Registry CRUD, built-in perspectives loaded, for_language filter, orchestrator uses registry |
| Graders | `tests/test_graders.py` | Each built-in grader, composite AND/OR, LLM rubric mock, EvalTask with grader config |

---

## Implementation Order

```
Phase 1: Adaptive Learning (independent, no dependencies)
Phase 2: Smart Dispatch (benefits from learning store for logging)
Phase 3: Workflow Discipline (benefits from dispatch for phase-aware routing)
Phase 4: Review & Eval (independent, can parallelize with Phase 2-3)
```

Phases 1 and 4 can run in parallel. Phases 2 and 3 depend on Phase 1 being available (optional integration with LearningStore).

---

## What This Does NOT Include

- LLM fine-tuning or model training (that's Neural-Guided Search spec)
- Grammar-based output constraints (that's DSL Grammar spec)
- Formal verification with Z3/Lean (that's Formal Verification spec)
- I/O example specifications (that's Programming by Example spec)
- TUI or frontend changes
- New CLI commands (dispatch is API-level, CLI integration is a follow-up)

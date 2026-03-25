# Neural-Guided Search Strategy

**Date:** 2026-03-24
**Status:** Proposal
**Layer:** 6 (Synthesis)

## Problem

Chimera's search strategies (TestConvergence, CEGIS, TreeSearch, Curriculum, Ensemble) all use the LLM as a general-purpose code generator. The LLM generates whole programs or edits, and the strategy controls iteration. But the LLM has no learned policy specific to Chimera's tool/environment configurations.

In neural-guided synthesis (DeepCoder, NSPS, R3NN), a trained model predicts which DSL constructs are likely to appear in the correct program, given the specification. This prediction narrows the search space before generation begins. The LLM replaces this in modern systems, but without fine-tuning on Chimera's specific synthesis traces, it's using generic priors rather than learned ones.

## What This Enables

- **Learned search heuristics**: Train a lightweight model on Chimera synthesis traces to predict which tools, file patterns, and code structures succeed for different spec types.
- **Warm-starting**: Before the LLM generates code, the learned policy predicts the likely shape of the solution (which files to create, which APIs to use, what test patterns to expect). This context steers the LLM.
- **Strategy selection**: Given a spec, predict which strategy (TestConvergence vs CEGIS vs TreeSearch) is most likely to succeed. Skip the hyperparameter search.
- **Cost reduction**: Fewer wasted iterations by steering the LLM toward productive regions of the search space.

## Design Sketch

### SynthesisTrace collection

First, collect traces from existing synthesis runs.

```python
@dataclass
class SynthesisTrace:
    """Record of a complete synthesis run."""
    spec_embedding: list[float]     # embedding of the spec description
    spec_features: dict             # structured features (num_tests, test_names, etc.)
    strategy_used: str
    tools_used: list[str]
    files_created: list[str]
    imports_used: list[str]
    epochs_to_converge: int | None  # None if failed
    total_cost: float
    success: bool

class TraceCollector(Callback):
    """Collect synthesis traces for training the search policy."""

    def on_synthesis_end(self, result: SynthesisResult) -> None:
        trace = self._build_trace(result)
        self._store(trace)
```

### SearchPolicy

A lightweight model that predicts synthesis configuration from spec features.

```python
class SearchPolicy:
    """Predict optimal synthesis configuration from spec features.

    Trained on SynthesisTrace data. Predicts:
    - Best strategy class
    - Likely tools needed
    - Expected file structure
    - Estimated epochs to convergence
    """

    def predict(self, spec: Spec) -> PolicyRecommendation:
        ...

    def train(self, traces: list[SynthesisTrace]) -> None:
        ...

    def warm_start_prompt(self, spec: Spec) -> str:
        """Generate context to prepend to the agent's system prompt.

        Based on similar successful traces, tells the agent:
        - What file structure to create
        - Which imports are likely needed
        - What patterns worked for similar specs
        """
        ...
```

### NeuralGuidedStrategy

```python
class NeuralGuidedStrategy(Strategy):
    """Strategy that uses a learned policy to guide synthesis.

    Before each epoch:
    1. Query the policy for recommendations
    2. Inject recommendations into the agent prompt
    3. Run standard synthesis with the guided prompt
    4. Record the trace for future training
    """

    def __init__(
        self,
        policy: SearchPolicy,
        fallback: Strategy = TestConvergence(),
        confidence_threshold: float = 0.7,
    ) -> None:
        ...
```

### Training the policy

The simplest version: nearest-neighbor over spec embeddings. No neural network needed initially.

```python
# Collect traces
collector = TraceCollector(store_path="traces.jsonl")
result = trainer.synthesize(callbacks=[collector])

# After accumulating traces, train policy
policy = SearchPolicy()
policy.train(TraceCollector.load("traces.jsonl"))

# Use in future runs
strategy = NeuralGuidedStrategy(policy=policy)
result = trainer.synthesize(strategy=strategy)
```

## Phased approach

1. **Phase 1: Trace collection** — TraceCollector callback, JSONL storage. Ship this first to start accumulating data.
2. **Phase 2: Nearest-neighbor policy** — Embed specs, find similar successful traces, use their configs. No ML required.
3. **Phase 3: Learned policy** — Train a small model (logistic regression or small transformer) on traces to predict strategy/tools/structure.
4. **Phase 4: Warm-start prompting** — Use policy predictions to inject context into the agent's prompt before generation.

## Open Questions

- How many traces are needed before the policy is useful? (Probably 50-100 for nearest-neighbor, 500+ for learned)
- What spec features are most predictive? (Number of tests, test names, NL description keywords, project structure)
- Should the policy be per-user (personalized) or global?
- How to handle concept drift as models and tools improve?

## Estimated Scope

Phase 1: ~100 lines (TraceCollector). Phase 2: ~200 lines (nearest-neighbor policy). Phases 3-4: ~300 lines each.

## References

- Balog et al., "DeepCoder: Learning to Write Programs" (ICLR 2017)
- Nye et al., "NSPS: Learning Compositional Rules via Neural Program Synthesis" (NeurIPS 2020)
- Parisotto et al., "Neuro-Symbolic Program Synthesis" (R3NN, ICLR 2017)
- Ellis et al., "DreamCoder: Bootstrapping Inductive Program Synthesis with Wake-Sleep Library Learning" (PLDI 2021)

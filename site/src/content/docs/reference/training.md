---
title: "chimera.training"
description: "Reference for chimera.training — Trainer, Spec, Architecture, and synthesis Strategies."
---

`chimera.training` is the synthesis layer: drive an agent through
multiple passes against a spec until tests pass.

## Top-level exports

```python
from chimera.training import (
    Trainer,
    Spec,
    Architecture,
    Constraint,
)
from chimera.training.strategies import (
    TestConvergenceStrategy,
    TreeSearchStrategy,
    CurriculumStrategy,
    EnsembleStrategy,
    MajorityVotingStrategy,
    AIMOEnsembleStrategy,
    PassthroughStrategy,
    CEGISStrategy,
    IncrementalStrategy,
)
```

| Symbol | Module | Purpose |
|---|---|---|
| `Trainer` | `chimera.training.trainer` | Synthesis orchestrator. `Trainer(agent_factory, strategy).run(spec)`. |
| `Spec` | `chimera.training.spec` | Task specification: description + tests + constraints. |
| `Architecture` | `chimera.training.architecture` | Multi-layer build composition (frontend / backend / tests / docs). |
| `Constraint` | `chimera.training.constraints` | Synthesis constraint base class (formal, example-based, type-based). |

## Strategies

| Strategy | Module | When to use |
|---|---|---|
| `TestConvergenceStrategy` | `chimera.training.strategies.test_convergence` | Iterate until all tests pass. Default. |
| `TreeSearchStrategy` | `chimera.training.strategies.tree_search` | Branch-and-prune over candidate solutions. |
| `CurriculumStrategy` | `chimera.training.strategies.curriculum` | Easy-first ordering of subtasks. |
| `EnsembleStrategy` | `chimera.training.strategies.ensemble` | Run N agents in parallel, pick best. |
| `MajorityVotingStrategy` | `chimera.training.strategies.majority_voting` | Pick the answer N agents agree on. |
| `AIMOEnsembleStrategy` | `chimera.training.strategies.aimo_ensemble` | AIMO-tuned ensemble. |
| `PassthroughStrategy` | `chimera.training.strategies.passthrough` | One pass, no retries. |
| `CEGISStrategy` | `chimera.training.strategies.cegis` | Counter-example-guided synthesis. |
| `IncrementalStrategy` | `chimera.training.strategies.incremental` | Build up sub-functions incrementally. |

## See also

- [`chimera.eval`](/reference/eval/) for benchmark-driven evaluation.
- [`chimera.composition`](/reference/composition/) for runtime
  composition (Pipeline / Ensemble / Supervisor) — strategies are about
  synthesis-time iteration.

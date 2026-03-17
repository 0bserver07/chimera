# Tuner

`chimera.training.tuner` provides hyperparameter search for synthesis
configurations.  `SynthesisTuner` performs grid search over a `SearchSpace`,
running synthesis for each configuration and selecting the best one.

---

## Key Classes

### SearchSpace

Defines the hyperparameter search space with categorical choices.
Calling `configurations()` produces the full Cartesian product (grid search).

```python
space = SearchSpace()
space.choice("model", ["claude-sonnet-4-20250514", "glm-5"])
space.choice("max_steps", [10, 25])
# Produces 4 configurations
```

### SynthesisTuner

Grid search over synthesis configurations.  Creates a fresh environment
for each trial, runs synthesis, and picks the best result.

Constructor parameters:

| Param | Type | Description |
|-------|------|-------------|
| `spec` | `Spec` | The synthesis specification |
| `env_factory` | `Callable[[], Environment]` | Returns a fresh env for each trial |
| `agent_factory` | `Callable[[dict], Agent] \| None` | Optional custom agent builder |

Key method: `search(space, max_trials, metric, callbacks) -> TunerResult`.

### TunerResult

| Field | Type | Description |
|-------|------|-------------|
| `best_config` | `dict` | Configuration that achieved the highest score |
| `best_score` | `float` | Score of the best configuration |
| `trials` | `list[TrialResult]` | All trial results in execution order |
| `total_cost` | `float` | Sum of costs across all trials |

## Usage

```python
from chimera.training.tuner import SynthesisTuner, SearchSpace

space = SearchSpace()
space.choice("model", ["claude-sonnet-4-20250514", "glm-5"])
space.choice("max_iterations", [5, 10, 20])

tuner = SynthesisTuner(
    spec=spec,
    env_factory=lambda: LocalEnvironment(workdir="/tmp/trial", test_cmd="pytest"),
)

result = tuner.search(space, max_trials=6, metric="pass_rate")
print(f"Best config: {result.best_config}")
print(f"Best score:  {result.best_score:.0%}")
print(f"Total cost:  ${result.total_cost:.2f}")
```

## Related

- [Training concepts](../concepts/training.md) -- strategies and the Trainer
- [Cost Tracking](cost-tracking.md) -- per-model pricing

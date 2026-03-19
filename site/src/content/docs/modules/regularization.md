---
title: "Regularization"
description: "Regularization"
---

`chimera.training.regularization` provides a callback that combines test
pass-rate with critic-based code quality scoring.  This lets strategies prefer
simpler, higher-quality solutions among those that pass the tests.

---

## Key Classes

### RegularizationCallback

A `Callback` that scores code quality after each epoch using a `Critic`.
When the epoch reaches a minimum pass rate, the critic evaluates the
generated code on readability, maintainability, and simplicity.

Constructor parameters:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `critic` | `Critic` | required | Critic used to evaluate code quality |
| `weight` | `float` | `0.3` | Weight given to the critic score (0.0-1.0) |
| `min_pass_rate` | `float` | `0.5` | Minimum pass rate before critic runs |

Key method:

- `combined_score(pass_rate, critic_score) -> float` -- returns
  `pass_rate * (1 - weight) + critic_score * weight`.

## Usage

```python
from chimera.training.regularization import RegularizationCallback
from chimera.critic.llm_critic import LLMCritic

critic = LLMCritic(provider=provider)
reg = RegularizationCallback(critic=critic, weight=0.3, min_pass_rate=0.5)

# Use as a callback during synthesis
result = trainer.synthesize(
    strategy=TestConvergence(max_iterations=20),
    callbacks=[reg],
)

# Compute a blended score manually
score = reg.combined_score(pass_rate=0.95, critic_score=0.8)
# => 0.95 * 0.7 + 0.8 * 0.3 = 0.905
```

## Related

- [Critic module](/critic/) -- LLMCritic and ChecklistCritic
- [Training concepts](/../concepts/training/) -- callbacks and strategies

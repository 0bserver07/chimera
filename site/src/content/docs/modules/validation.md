---
title: "Validation"
description: "Validation"
---

`chimera.training.validation` splits a test suite into training and validation
sets to detect overfitting during synthesis.  The agent synthesizes against
training tests only; validation tests are held out and evaluated afterwards.

---

## Key Classes

### ValidationSplit

Splits test files from a `Spec.tests_dir` into two temporary directories.
The split is done at the **file** level (not individual test functions) to
avoid import/fixture issues.

Constructor parameters:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `spec` | `Spec` | required | A Spec with `tests_dir` set |
| `ratio` | `float` | `0.3` | Fraction of test files held out for validation |
| `seed` | `int \| None` | `None` | Random seed for reproducible splits |

Key properties: `train_spec`, `val_spec`, `train_files`, `val_files`.

### ValidationResult

Dataclass returned by `ValidationSplit.evaluate()`:

| Field | Type | Description |
|-------|------|-------------|
| `train_pass_rate` | `float` | Pass rate on training tests |
| `val_pass_rate` | `float` | Pass rate on held-out validation tests |
| `overfit_gap` | `float` | `train_pass_rate - val_pass_rate` |
| `train_passed` / `val_passed` | `int` | Counts of passing tests |
| `train_total` / `val_total` | `int` | Total test counts |

## Usage

```python
from chimera.training.spec import Spec
from chimera.training.validation import ValidationSplit

spec = Spec.from_tests("tests/", description="Build a calculator")
split = ValidationSplit(spec, ratio=0.3, seed=42)

# Use split.train_spec for synthesis (agent never sees validation tests)
result = trainer.synthesize(spec=split.train_spec, ...)

# Evaluate on held-out tests
val_result = split.evaluate(env)
print(f"Train: {val_result.train_pass_rate:.0%}")
print(f"Val:   {val_result.val_pass_rate:.0%}")
print(f"Overfit gap: {val_result.overfit_gap:.0%}")
```

## Related

- [Training concepts](../concepts/training.md) -- strategies and the synthesis loop
- [Spec Inference](spec-inference.md) -- auto-generate tests from source
- `examples/validation_split.py` -- runnable example

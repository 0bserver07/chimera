---
title: "Oracle"
description: "Oracle"
---

`chimera.training.oracle` provides a callback that grows the test suite
during synthesis.  After each epoch where all tests pass, `OracleCallback`
generates new test cases targeting edge cases -- forcing the agent to handle
progressively harder inputs.

---

## Key Classes

### OracleCallback

A `Callback` that generates adversarial tests after successful epochs.
Supports two modes:

| Mode | Description |
|------|-------------|
| `"llm"` | Uses a Provider to generate adversarial tests via LLM |
| `"property"` | Generates simple property-based test stubs from signatures |

Constructor parameters:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | `Provider \| None` | `None` | LLM provider (required for `"llm"` mode) |
| `tests_dir` | `str \| None` | `None` | Directory to write generated test files |
| `max_new_tests_per_epoch` | `int` | `3` | Max new test functions per epoch |
| `mode` | `"llm" \| "property"` | `"llm"` | Generation mode |

Key attribute: `generated_tests` -- list of all test code strings generated
so far.

## Usage

```python
from chimera.training.oracle import OracleCallback

# LLM mode: generates adversarial edge-case tests
oracle = OracleCallback(
    provider=provider,
    tests_dir="tests/",
    max_new_tests_per_epoch=3,
    mode="llm",
)

result = trainer.synthesize(
    strategy=TestConvergence(max_iterations=20),
    callbacks=[oracle],
)

print(f"Generated {len(oracle.generated_tests)} new tests during synthesis")
```

```python
# Property mode: no LLM needed, generates type-check stubs
oracle = OracleCallback(
    tests_dir="tests/",
    mode="property",
)
```

## Related

- [Training concepts](/../concepts/training/) -- callbacks and the synthesis loop
- [Validation](/validation/) -- detect overfitting with held-out tests
- [Spec Inference](/spec-inference/) -- infer invariants from source code

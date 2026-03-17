# Spec Inference

`chimera.training.spec_inference` infers specifications from existing source
code via static analysis.  `SpecInferrer` analyzes function signatures,
return statements, and patterns to generate invariant-based regression tests.
All analysis is AST-based -- no code execution required.

---

## Key Classes

### InferredInvariant

Dataclass representing an invariant discovered from source code.

| Field | Type | Description |
|-------|------|-------------|
| `function` | `str` | Function name the invariant applies to |
| `file` | `str` | Source file path |
| `invariant` | `str` | Human-readable description (e.g. "returns int") |
| `pattern` | `str` | Machine pattern: `return_type`, `non_null`, `has_docstring`, `argument_count` |
| `confidence` | `float` | 0.0 to 1.0 |
| `test_code` | `str` | Generated test function source |

### SpecInferrer

Analyzes Python source and infers invariants.  Detected patterns:

- **return_type** -- function has type annotations
- **non_null** -- function never returns `None`
- **has_docstring** -- function has a docstring (regression guard)
- **argument_count** -- fixed number of parameters

Key methods:

- `analyze(source, file_path) -> list[InferredInvariant]`
- `generate_test_file(invariants) -> str`
- `write_test_file(output_path, invariants) -> str`

## Usage

```python
from chimera.training.spec_inference import SpecInferrer

inferrer = SpecInferrer()

source = open("calculator.py").read()
invariants = inferrer.analyze(source, file_path="calculator.py")

for inv in invariants:
    print(f"{inv.function}: {inv.invariant} (confidence: {inv.confidence})")

# Generate a regression test file
test_content = inferrer.generate_test_file()
print(test_content)

# Or write directly to disk
inferrer.write_test_file("tests/test_invariants.py")
```

## Related

- [Oracle](oracle.md) -- grow the test suite during synthesis
- [Mutation Testing](mutation-testing.md) -- evaluate test suite quality
- [Validation](validation.md) -- detect overfitting with held-out tests

---
title: "Mutation Testing"
description: "Mutation Testing"
---

`chimera.training.mutation` generates code mutations and checks whether
the test suite catches them.  A high mutation score means the tests are
thorough; surviving mutants reveal weak spots in test coverage.

---

## Key Classes

### MutationResult

Dataclass returned by `MutationTester.run()`.

| Field | Type | Description |
|-------|------|-------------|
| `total_mutants` | `int` | Number of mutations generated |
| `killed` | `int` | Mutations caught by tests |
| `survived` | `int` | Mutations NOT caught by tests |
| `mutation_score` | `float` | `killed / total` (higher is better) |
| `survivors` | `list[Mutation]` | Mutations that were not detected |

### MutationTester

Generates mutations from Python source code using AST transformations.
Supported mutation operators:

- **Operator swap** -- `+` to `-`, `*` to `/`, `==` to `!=`, etc.
- **Comparison swap** -- `<` to `<=`, `>` to `>=`, etc.
- **Condition negation** -- `if x` to `if not x`

Constructor: `MutationTester(max_mutants=50)`.

Key methods:

- `generate_mutants(source, file_path) -> list[Mutation]`
- `run(source, test_fn, file_path) -> MutationResult`

## Usage

```python
from chimera.training.mutation import MutationTester

tester = MutationTester(max_mutants=30)

source = open("calculator.py").read()

# Just see what mutations would be generated
result = tester.run(source, file_path="calculator.py")
print(f"Generated {result.total_mutants} mutants")

# With a test function to check if tests catch mutations
def run_tests(mutation):
    # Return True if tests PASS (mutation survived)
    # Return False if tests FAIL (mutation killed)
    ...

result = tester.run(source, test_fn=run_tests)
print(f"Mutation score: {result.mutation_score:.0%}")
print(f"Survivors: {result.survived}")
```

## Related

- [Spec Inference](/spec-inference/) -- auto-generate tests from invariants
- [Fault Localization](/fault-localization/) -- find where bugs are
- [Validation](/validation/) -- detect overfitting with held-out tests

<!-- W17-3 SOURCE FOOTER -->

## Source

`chimera/training/mutation.py` -- verified against current source at wave-17.

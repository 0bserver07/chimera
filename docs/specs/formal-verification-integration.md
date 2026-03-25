# Formal Verification Integration (Z3 / Lean)

**Date:** 2026-03-24
**Status:** Proposal
**Layer:** 4 (Tools) / 5 (Evaluation)

## Problem

Chimera's verification is test-based. Tests check behavior on specific inputs. They don't prove properties hold for all inputs. A generated function can pass 100 tests and still be wrong on the 101st case.

Classical synthesis uses formal verifiers (SMT solvers, proof assistants) to close this gap. Z3 can prove that a function satisfies a postcondition for all inputs in a decidable theory. Lean 4 can verify multi-step mathematical proofs. Bourbaki already integrates both — but as a separate project. Chimera should be able to use formal verification as a tool within its synthesis loop.

## What This Enables

- **Stronger verification in the CEGIS loop**: Instead of test failures as counterexamples, Z3 can generate counterexamples that cover the entire input space.
- **Property-based specs**: Define specs as logical properties (e.g., "for all x > 0, f(x) > 0") not just tests.
- **Hybrid verification**: Run tests first (fast, cheap), escalate to formal verification for critical properties.
- **Bridge to Bourbaki**: Reuse Bourbaki's Lean and SymPy integration as Chimera tools.

## Design Sketch

### Z3VerifierTool

A new tool that wraps Z3 for property checking.

```python
class Z3VerifierTool(Tool):
    """Verify properties of generated code using Z3.

    Given a function and a property (expressed as a Python predicate
    with symbolic variables), checks whether the property holds for
    all inputs. Returns VERIFIED, COUNTEREXAMPLE (with the failing
    input), or UNKNOWN (timeout).
    """

    name = "z3_verify"

    def execute(self, function_path: str, property_spec: str, timeout: int = 30) -> ToolResult:
        ...
```

### LeanVerifierTool

```python
class LeanVerifierTool(Tool):
    """Verify formal proofs using Lean 4.

    Sends Lean code to a Lean subprocess, parses the output.
    Checks: return code 0, no error-level messages, no `sorry`.
    Same interface as Bourbaki's Lean integration.
    """

    name = "lean_verify"

    def execute(self, lean_code: str, timeout: int = 30) -> ToolResult:
        ...
```

### FormalSpec

A Spec variant that includes verifiable properties alongside tests.

```python
class FormalSpec(Spec):
    """Spec with formal properties verified by Z3/Lean.

    Properties are checked after tests pass. A candidate that passes
    all tests but fails a formal property gets the property violation
    as feedback for the next iteration.
    """

    def __init__(self, tests_dir: str, properties: list[Property], description: str = "") -> None:
        ...
```

### Integration with CEGIS

When a Z3 check returns a counterexample, it becomes the next test case:

```python
# In CEGISStrategy
result = z3_verify(function, property)
if result.counterexample:
    new_test = generate_test_from_counterexample(result.counterexample)
    spec.add_test(new_test)  # grows the test suite
```

This connects the formal verification spec to the oracle/growing-test-suite design from the ML primitives plan.

## Dependencies

- Z3 Python bindings (`z3-solver` package) — optional dependency
- Lean 4 installation — optional, checked at runtime
- Bourbaki's Lean integration could be extracted as a shared package

## Open Questions

- How to express properties in a way that's natural for coding agents (Python predicates? docstring annotations? separate spec files?)
- Should Z3 be a tool the agent calls, or a verification step the Trainer runs automatically?
- Performance: Z3 can be slow on complex properties. What's the timeout strategy?
- How to handle UNKNOWN results (Z3 timeout)?

## Estimated Scope

~200-300 lines per verifier tool. ~150 lines for FormalSpec. Optional dependencies only.

## References

- de Moura & Bjørner, "Z3: An Efficient SMT Solver" (2008)
- Bourbaki project — existing Lean 4 + SymPy integration
- CEGIS (Solar-Lezama) — counterexample-guided synthesis with formal verification

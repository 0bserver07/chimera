# Programming by Example (PBE) Spec Mode

**Date:** 2026-03-24
**Status:** Proposal
**Layer:** 6 (Synthesis)

## Problem

Chimera's Spec is test-driven: you write test files, the agent makes them pass. But programming by example — the most studied form of specification in synthesis research — uses input-output pairs directly. The user provides examples like `("William Henry Charles" → "Charles, W.")` and the system infers the transformation.

This is different from tests. Tests are code. I/O examples are data. The synthesis system generates both the program AND the tests from the examples. FlashFill, DeepCoder, NSPS, and RobustFill all work this way.

Chimera should support I/O examples as a first-class spec type, not just as something you manually convert into test files.

## What This Enables

- **Lower barrier to spec writing**: Users provide examples, not test code.
- **Automatic test generation from examples**: The spec generates tests, then synthesis proceeds as normal.
- **Held-out validation**: Split examples into train/val sets (connects to the validation splits design).
- **Bridge to PBE benchmarks**: Run Chimera on FlashFill-style and DeepCoder-style benchmarks.
- **Compositional synthesis**: When direct synthesis fails on hard examples, decompose into subtasks (per the LLM-Guided Compositional Synthesis paper).

## Design Sketch

### ExampleSpec

```python
@dataclass
class Example:
    """A single input-output example."""
    inputs: dict[str, Any]    # named inputs
    output: Any               # expected output
    description: str = ""     # optional NL description of this case

class ExampleSpec(Spec):
    """Spec from input-output examples.

    Generates test code from examples, then delegates to
    standard test-based synthesis. The generated tests use
    parametrize for clean reporting.
    """

    def __init__(
        self,
        examples: list[Example],
        function_name: str,
        description: str = "",
        signature: str | None = None,  # optional type hint
    ) -> None:
        ...

    @classmethod
    def from_csv(cls, path: str, input_cols: list[str], output_col: str) -> ExampleSpec:
        """Load examples from CSV."""

    @classmethod
    def from_json(cls, path: str) -> ExampleSpec:
        """Load examples from JSON array of {inputs: {}, output: ...}."""

    def to_tests(self, output_dir: str) -> Spec:
        """Generate pytest file from examples and return a standard Spec."""

    def split(self, ratio: float = 0.3, seed: int | None = None) -> tuple[ExampleSpec, ExampleSpec]:
        """Split into train/validation example sets."""
```

### Generated test format

```python
# Auto-generated from ExampleSpec
import pytest

@pytest.mark.parametrize("inputs,expected", [
    ({"name": "William Henry Charles"}, "Charles, W."),
    ({"name": "John Smith"}, "Smith, J."),
    ({"name": "Mary"}, "Mary"),
])
def test_format_name(inputs, expected):
    from solution import format_name
    assert format_name(**inputs) == expected
```

### Usage

```python
spec = ExampleSpec(
    examples=[
        Example(inputs={"name": "William Henry Charles"}, output="Charles, W."),
        Example(inputs={"name": "John Smith"}, output="Smith, J."),
    ],
    function_name="format_name",
    description="Format a full name as 'Last, F.'"
)

# Standard synthesis from here
result = trainer.synthesize(spec=spec)

# Or split for validation
train, val = spec.split(ratio=0.3)
result = trainer.synthesize(spec=train)
val_results = val.to_tests("/tmp/val").run(env)
```

## Integration with Compositional Synthesis

When PBE synthesis fails (agent can't find a single function for all examples), decompose:

1. Cluster examples by pattern
2. Synthesize a function for each cluster
3. Synthesize a dispatcher that routes inputs to the right function

This follows the LLM-Guided Compositional Synthesis paper's ForwardAll/Forward1/Backward1 strategies.

## Open Questions

- How to handle examples with complex types (dataframes, images, file contents)?
- Should ExampleSpec support negative examples ("this input should NOT produce this output")?
- How to generate good test names from I/O pairs?

## Estimated Scope

~200 lines for ExampleSpec. ~100 lines for CSV/JSON loaders. ~100 lines for test generation.

## References

- Gulwani, "Automating String Processing in Spreadsheets Using Input-Output Examples" (POPL 2011)
- Balog et al., "DeepCoder: Learning to Write Programs" (ICLR 2017)
- Nye et al., "Learning Compositional Rules via Neural Program Synthesis" (NeurIPS 2020)
- Khan et al., "LLM-Guided Compositional Program Synthesis" (2025)

# Formal DSL / Grammar Definition Layer

**Date:** 2026-03-24
**Status:** Proposal
**Layer:** 4 (Agent) / 6 (Synthesis)

## Problem

Chimera's search space constraints are implicit. Tool sets and environments limit what the agent can express, but there's no way to define or reason about these constraints formally. You can't say "the agent may only produce Python functions that call these specific APIs" as an explicit grammar.

In classical synthesis (FlashFill, Sketch, SyGuS), a DSL grammar is the primary mechanism for making search tractable. The grammar prunes the space before search begins. Chimera relies on the LLM's priors plus tool/environment constraints to achieve the same effect, but this is opaque — you can't inspect, compare, or compose search space constraints the way you can compose grammars.

## What This Enables

- **Explicit search space definition**: Define what the agent is allowed to produce, not just what tools it can use.
- **Grammar composition**: Combine grammars the same way you combine tool sets — swap, extend, restrict.
- **Static analysis of agent capabilities**: Before running an agent, analyze what programs it can express.
- **SyGuS-compatible specs**: Bridge to the formal synthesis community's benchmarks and tools.
- **Tighter tool constraints**: Instead of "agent has bash", express "agent may invoke `pytest` and `git diff` but not `rm`" as grammar rules.

## Design Sketch

### GrammarConstraint

A new `Constraint` subclass that validates agent output against a grammar.

```python
class GrammarConstraint(Constraint):
    """Constrain agent output to programs expressible in a grammar.

    The grammar defines:
    - Allowed language constructs (imports, function signatures, control flow)
    - Allowed external calls (which APIs, which CLI commands)
    - Output structure (module layout, file patterns)
    """

    def __init__(self, grammar: Grammar) -> None:
        self._grammar = grammar

    def check(self, env: Environment) -> ConstraintResult:
        """Parse generated code and check against grammar rules."""
        ...
```

### Grammar definition

```python
grammar = Grammar(
    allowed_imports=["fastapi", "pydantic", "sqlite3"],
    allowed_calls=["pytest", "git diff", "git commit"],
    disallowed_patterns=["eval(", "exec(", "subprocess.run"],
    max_function_length=50,
    required_structure={"src/": ["main.py", "models.py"], "tests/": ["test_*.py"]},
)
```

### Integration points

- `Spec` can include a grammar alongside tests and NL description
- `Trainer` validates each epoch's output against the grammar before running tests
- Grammar violations produce structured feedback (which rule was violated, where)
- Grammars compose: `grammar_a + grammar_b` merges allowed constructs

## Open Questions

- How much of this can be done with AST analysis vs requiring a formal parser?
- Should grammars be per-file or per-project?
- How does this interact with existing `Constraint` scoring (the regularization design)?
- Is there value in generating grammars automatically from an existing codebase?

## Estimated Scope

~300-400 lines. Grammar class, GrammarConstraint, AST-based checker, composition operators.

## References

- Solar-Lezama, "Program Sketching" (2008) — grammar-constrained synthesis
- Alur et al., SyGuS — syntax-guided synthesis framework
- Gulwani, FlashFill (2011) — DSL design for tractable search

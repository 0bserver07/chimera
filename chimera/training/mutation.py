from __future__ import annotations

import ast
import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Mutation:
    """A single code mutation."""
    file: str
    line: int
    original: str
    mutated: str
    operator: str  # e.g. "swap_operator", "negate_condition"
    killed: bool = False  # True if tests caught it


@dataclass
class MutationResult:
    """Result of mutation testing."""
    total_mutants: int
    killed: int
    survived: int
    mutation_score: float  # killed / total
    survivors: list[Mutation] = field(default_factory=list)


class MutationTester:
    """Generate code mutations and check if tests catch them."""

    # Operator swaps
    _OP_SWAPS: dict[type, type] = {
        ast.Add: ast.Sub,
        ast.Sub: ast.Add,
        ast.Mult: ast.Div,
        ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq,
        ast.Lt: ast.LtE,
        ast.LtE: ast.Lt,
        ast.Gt: ast.GtE,
        ast.GtE: ast.Gt,
    }

    def __init__(self, max_mutants: int = 50) -> None:
        self._max_mutants = max_mutants

    def generate_mutants(self, source: str, file_path: str = "<source>") -> list[Mutation]:
        """Generate mutations from source code using AST.

        Args:
            source: Python source code to analyze.
            file_path: File path for reporting.

        Returns:
            List of Mutation objects representing possible mutations.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        lines = source.splitlines()
        mutants: list[Mutation] = []

        for node in ast.walk(tree):
            if len(mutants) >= self._max_mutants:
                break

            # Swap binary operators: + -> -, * -> /, etc.
            if isinstance(node, ast.BinOp) and type(node.op) in self._OP_SWAPS:
                line_idx = node.lineno - 1
                if 0 <= line_idx < len(lines):
                    original_line = lines[line_idx]
                    op_name = type(node.op).__name__
                    swap_name = self._OP_SWAPS[type(node.op)].__name__
                    # Create mutated source
                    new_tree = copy.deepcopy(tree)
                    for n in ast.walk(new_tree):
                        if (
                            isinstance(n, ast.BinOp)
                            and n.lineno == node.lineno
                            and n.col_offset == node.col_offset
                        ):
                            n.op = self._OP_SWAPS[type(node.op)]()
                            break
                    try:
                        mutated_source = ast.unparse(new_tree)
                        mutated_lines = mutated_source.splitlines()
                        mutated_line = (
                            mutated_lines[line_idx]
                            if line_idx < len(mutated_lines)
                            else original_line
                        )
                    except Exception:
                        mutated_line = f"<mutated {op_name}->{swap_name}>"
                    mutants.append(Mutation(
                        file=file_path,
                        line=node.lineno,
                        original=original_line.strip(),
                        mutated=mutated_line.strip(),
                        operator=f"swap_{op_name}_to_{swap_name}",
                    ))

            # Swap comparison operators
            if isinstance(node, ast.Compare) and len(node.ops) == 1:
                op = node.ops[0]
                if type(op) in self._OP_SWAPS:
                    line_idx = node.lineno - 1
                    if 0 <= line_idx < len(lines):
                        mutants.append(Mutation(
                            file=file_path,
                            line=node.lineno,
                            original=lines[line_idx].strip(),
                            mutated=f"<swapped {type(op).__name__}>",
                            operator=f"swap_{type(op).__name__}",
                        ))

            # Negate conditions: `if x` -> `if not x`
            if isinstance(node, ast.If) and not isinstance(node.test, ast.UnaryOp):
                line_idx = node.lineno - 1
                if 0 <= line_idx < len(lines):
                    mutants.append(Mutation(
                        file=file_path,
                        line=node.lineno,
                        original=lines[line_idx].strip(),
                        mutated="<negated condition>",
                        operator="negate_condition",
                    ))

        return mutants[:self._max_mutants]

    def run(
        self,
        source: str,
        test_fn: Any = None,
        file_path: str = "<source>",
    ) -> MutationResult:
        """Generate mutants and optionally test them.

        Args:
            source: Source code to mutate.
            test_fn: Optional callable(Mutation) -> bool. Returns True if tests pass.
                     If None, mutants are generated but not tested.
            file_path: File path for reporting.

        Returns:
            MutationResult with score and survivor details.
        """
        mutants = self.generate_mutants(source, file_path)

        if test_fn is None:
            # Just report what we'd mutate
            return MutationResult(
                total_mutants=len(mutants),
                killed=0,
                survived=len(mutants),
                mutation_score=0.0,
                survivors=mutants,
            )

        killed = 0
        survivors = []
        for m in mutants:
            # For each mutation, check if tests catch it.
            # In a real implementation, you'd apply the mutation to a temp file,
            # run tests, and check. Here we use the test_fn callback.
            try:
                tests_pass = test_fn(m)
                if tests_pass:
                    # Mutation survived -- tests didn't catch it!
                    survivors.append(m)
                else:
                    m.killed = True
                    killed += 1
            except Exception:
                m.killed = True
                killed += 1

        total = len(mutants)
        return MutationResult(
            total_mutants=total,
            killed=killed,
            survived=total - killed,
            mutation_score=killed / total if total > 0 else 1.0,
            survivors=survivors,
        )

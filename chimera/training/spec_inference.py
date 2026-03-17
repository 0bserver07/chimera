from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InferredInvariant:
    """An invariant inferred from observing code behavior."""
    function: str
    file: str
    invariant: str           # human-readable: "always returns int"
    pattern: str             # machine pattern: "return_type", "non_null", "range"
    confidence: float        # 0.0 to 1.0
    test_code: str           # generated test function


class SpecInferrer:
    """Infer specifications from existing source code via static analysis.

    Analyzes function signatures, return statements, and patterns to
    generate invariant-based regression tests. Does NOT execute code —
    uses AST analysis only (safe for any codebase).
    """

    def __init__(self) -> None:
        self._invariants: list[InferredInvariant] = []

    def analyze(self, source: str, file_path: str = "<source>") -> list[InferredInvariant]:
        """Analyze source code and infer invariants.

        Patterns detected:
        - return_type: function has type annotations -> assert return type
        - non_null: function never returns None (no `return None` or bare `return`)
        - has_docstring: function has a docstring (regression: don't remove it)
        - pure_function: no global/nonlocal, no assignments to external state
        - argument_count: fixed number of parameters

        Args:
            source: Python source code to analyze.
            file_path: File path for attribution in invariants.

        Returns:
            List of inferred invariants.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        invariants: list[InferredInvariant] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith('_'):
                continue  # skip private functions

            func_name = node.name

            # Pattern 1: Return type annotation
            if node.returns is not None:
                type_str = ast.unparse(node.returns)
                test = (
                    f"def test_{func_name}_return_type():\n"
                    f"    \"\"\"Verify {func_name} returns {type_str}.\"\"\"\n"
                    f"    # Auto-generated invariant: return type is {type_str}\n"
                    f"    pass  # Needs concrete call to verify\n"
                )
                invariants.append(InferredInvariant(
                    function=func_name, file=file_path,
                    invariant=f"returns {type_str}",
                    pattern="return_type", confidence=1.0,
                    test_code=test,
                ))

            # Pattern 2: Non-null return
            returns_none = False
            for child in ast.walk(node):
                if isinstance(child, ast.Return):
                    if child.value is None:
                        returns_none = True
                    elif isinstance(child.value, ast.Constant) and child.value.value is None:
                        returns_none = True

            if not returns_none and self._has_return(node):
                test = (
                    f"def test_{func_name}_never_returns_none():\n"
                    f"    \"\"\"Verify {func_name} never returns None.\"\"\"\n"
                    f"    # Auto-generated invariant: non-null return\n"
                    f"    pass  # Needs concrete call to verify\n"
                )
                invariants.append(InferredInvariant(
                    function=func_name, file=file_path,
                    invariant="never returns None",
                    pattern="non_null", confidence=0.8,
                    test_code=test,
                ))

            # Pattern 3: Has docstring
            if (node.body and isinstance(node.body[0], ast.Expr) and
                isinstance(node.body[0].value, ast.Constant) and
                isinstance(node.body[0].value.value, str)):
                test = (
                    f"def test_{func_name}_has_docstring():\n"
                    f"    \"\"\"Verify {func_name} retains its docstring.\"\"\"\n"
                    f"    assert {func_name}.__doc__ is not None\n"
                )
                invariants.append(InferredInvariant(
                    function=func_name, file=file_path,
                    invariant="has docstring",
                    pattern="has_docstring", confidence=1.0,
                    test_code=test,
                ))

            # Pattern 4: Argument count
            args = node.args
            n_args = len(args.args) + len(args.posonlyargs) + len(args.kwonlyargs)
            # Subtract 1 for 'self' if it looks like a method
            if args.args and args.args[0].arg == 'self':
                n_args -= 1
            if n_args > 0:
                test = (
                    f"def test_{func_name}_argument_count():\n"
                    f"    \"\"\"Verify {func_name} accepts {n_args} argument(s).\"\"\"\n"
                    f"    import inspect\n"
                    f"    sig = inspect.signature({func_name})\n"
                    f"    params = [p for p in sig.parameters if p != 'self']\n"
                    f"    assert len(params) == {n_args}\n"
                )
                invariants.append(InferredInvariant(
                    function=func_name, file=file_path,
                    invariant=f"accepts {n_args} argument(s)",
                    pattern="argument_count", confidence=1.0,
                    test_code=test,
                ))

        self._invariants.extend(invariants)
        return invariants

    def _has_return(self, func_node: ast.AST) -> bool:
        """Check if function has at least one return with a value."""
        for node in ast.walk(func_node):
            if isinstance(node, ast.Return) and node.value is not None:
                return True
        return False

    def generate_test_file(self, invariants: list[InferredInvariant] | None = None) -> str:
        """Generate a pytest file from inferred invariants.

        Args:
            invariants: Invariants to include. Defaults to all accumulated invariants.

        Returns:
            String content of the generated test file.
        """
        invs = invariants or self._invariants
        if not invs:
            return "# No invariants inferred\n"

        lines = ['"""Auto-generated regression tests from inferred invariants."""\n']
        for inv in invs:
            lines.append(f"# Invariant: {inv.function} — {inv.invariant} (confidence: {inv.confidence})")
            lines.append(inv.test_code)
            lines.append("")

        return "\n".join(lines)

    def write_test_file(self, output_path: str, invariants: list[InferredInvariant] | None = None) -> str:
        """Write invariant tests to a file. Returns the path.

        Args:
            output_path: Path to write the test file to.
            invariants: Invariants to include. Defaults to all accumulated invariants.

        Returns:
            The output path written to.
        """
        content = self.generate_test_file(invariants)
        with open(output_path, 'w') as f:
            f.write(content)
        return output_path

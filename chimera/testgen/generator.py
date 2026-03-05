"""Test case generation from source code analysis."""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TestCase:
    """A generated test case."""
    name: str
    target_function: str
    target_file: str
    test_code: str
    category: str = "unit"  # unit, edge, error


class TestGenerator:
    """Generates test case skeletons from source code analysis.

    Analyzes Python source files to extract function signatures
    and generate test stubs.
    """

    def __init__(self) -> None:
        self._test_cases: list[TestCase] = []

    def analyze(self, filepath: str) -> list[TestCase]:
        """Analyze a Python file and generate test cases.

        Args:
            filepath: Path to Python source file.

        Returns:
            List of generated TestCase instances.
        """
        source = Path(filepath).read_text()
        return self.analyze_source(source, filepath)

    def analyze_source(self, source: str, filepath: str = "<unknown>") -> list[TestCase]:
        """Analyze Python source code and generate test cases."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        cases: list[TestCase] = []
        module_name = Path(filepath).stem

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                cases.extend(self._generate_for_function(node, filepath, module_name))
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                        cases.extend(self._generate_for_method(
                            item, node.name, filepath, module_name,
                        ))

        self._test_cases.extend(cases)
        return cases

    def _generate_for_function(self, node: ast.FunctionDef, filepath: str,
                                module_name: str) -> list[TestCase]:
        """Generate test cases for a standalone function."""
        cases = []
        func_name = node.name
        args = [a.arg for a in node.args.args if a.arg != "self"]

        # Basic test
        cases.append(TestCase(
            name=f"test_{func_name}",
            target_function=func_name,
            target_file=filepath,
            test_code=self._make_test_stub(func_name, args, module_name),
            category="unit",
        ))

        # Edge case with None/empty args
        if args:
            cases.append(TestCase(
                name=f"test_{func_name}_edge_empty",
                target_function=func_name,
                target_file=filepath,
                test_code=self._make_edge_test(func_name, args, module_name),
                category="edge",
            ))

        # Error case
        cases.append(TestCase(
            name=f"test_{func_name}_error",
            target_function=func_name,
            target_file=filepath,
            test_code=self._make_error_test(func_name, module_name),
            category="error",
        ))

        return cases

    def _generate_for_method(self, node: ast.FunctionDef, class_name: str,
                              filepath: str, module_name: str) -> list[TestCase]:
        """Generate test cases for a class method."""
        func_name = node.name
        args = [a.arg for a in node.args.args if a.arg != "self"]

        return [TestCase(
            name=f"test_{class_name}_{func_name}",
            target_function=f"{class_name}.{func_name}",
            target_file=filepath,
            test_code=self._make_method_test(class_name, func_name, args, module_name),
            category="unit",
        )]

    def _make_test_stub(self, func_name: str, args: list[str], module: str) -> str:
        arg_str = ", ".join(f"{a}=None" for a in args) if args else ""
        return (
            f"def test_{func_name}():\n"
            f"    # TODO: provide real arguments\n"
            f"    result = {func_name}({arg_str})\n"
            f"    assert result is not None\n"
        )

    def _make_edge_test(self, func_name: str, args: list[str], module: str) -> str:
        return (
            f"def test_{func_name}_edge_empty():\n"
            f"    # Test with edge-case inputs\n"
            f"    # TODO: adjust for actual parameter types\n"
            f"    pass\n"
        )

    def _make_error_test(self, func_name: str, module: str) -> str:
        return (
            f"def test_{func_name}_error():\n"
            f"    # Test error handling\n"
            f"    # TODO: test with invalid inputs\n"
            f"    pass\n"
        )

    def _make_method_test(self, class_name: str, func_name: str,
                           args: list[str], module: str) -> str:
        return (
            f"def test_{class_name}_{func_name}():\n"
            f"    obj = {class_name}()\n"
            f"    # TODO: provide real arguments\n"
            f"    result = obj.{func_name}()\n"
            f"    assert result is not None\n"
        )

    @property
    def test_cases(self) -> list[TestCase]:
        return list(self._test_cases)

    def clear(self) -> None:
        self._test_cases.clear()

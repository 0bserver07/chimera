"""Python language parser using the ast module."""
from __future__ import annotations

import ast

from chimera.tools.parsers.base import LanguageParser, Symbol


class PythonParser(LanguageParser):
    """Parse Python source using the ast module."""

    extensions = (".py",)

    def parse(self, source: str) -> list[Symbol]:
        """Parse Python source and extract symbols.

        Args:
            source: Python source code text.

        Returns:
            List of top-level symbols with nested children.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        symbols: list[Symbol] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                cls_sym = Symbol(name=node.name, kind="class")
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_sym = Symbol(
                            name=self._format_function(child), kind="method"
                        )
                        cls_sym.children.append(method_sym)
                symbols.append(cls_sym)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sig = self._format_function(node)
                symbols.append(Symbol(name=sig, kind="function"))

        return symbols

    def _format_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> str:
        args = self._format_args(node.args)
        ret = ""
        if node.returns:
            ret = f" -> {ast.unparse(node.returns)}"
        return f"{node.name}({args}){ret}"

    def _format_args(self, args: ast.arguments) -> str:
        parts: list[str] = []
        for arg in args.args:
            s = arg.arg
            if arg.annotation:
                s += f": {ast.unparse(arg.annotation)}"
            parts.append(s)
        return ", ".join(parts)

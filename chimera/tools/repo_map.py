# chimera/tools/repo_map.py
"""Repository mapping — structural overview of a codebase."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.tools.parsers import GoParser, LanguageParser, PythonParser, RustParser, TypeScriptParser
from chimera.tools.parsers.base import Symbol
from chimera.types import ToolResult

IGNORE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules", ".venv", "venv", ".tox", ".eggs",
    "dist", "build", ".chimera_checkpoints",
}

_PARSERS: dict[str, LanguageParser] = {}
for _parser_cls in (PythonParser, TypeScriptParser, GoParser, RustParser):
    _parser = _parser_cls()
    for _ext in _parser.extensions:
        _PARSERS[_ext] = _parser


class RepoMap:
    """Generate a structural overview of a codebase.

    For Python files, extracts class and function signatures using the ast
    module. For other files, lists paths only.
    """

    def __init__(self, root: str, max_depth: int | None = None) -> None:
        self.root = Path(root)
        self.max_depth = max_depth

    def generate(self) -> str:
        """Generate the repo map as a formatted string."""
        lines: list[str] = []
        self._walk(self.root, lines, depth=0)
        return "\n".join(lines)

    def _walk(self, path: Path, lines: list[str], depth: int) -> None:
        if self.max_depth is not None and depth > self.max_depth:
            return

        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        for entry in entries:
            if entry.name in IGNORE_DIRS:
                continue
            if entry.name.startswith("."):
                continue

            rel = entry.relative_to(self.root)

            if entry.is_dir():
                self._walk(entry, lines, depth + 1)
            else:
                indent = "  " * depth
                lines.append(f"{indent}{rel}")
                parser = _PARSERS.get(entry.suffix)
                if parser is not None:
                    self._parse_with(parser, entry, lines, depth + 1)

    def _parse_with(
        self, parser: LanguageParser, path: Path, lines: list[str], depth: int
    ) -> None:
        try:
            source = path.read_text()
        except UnicodeDecodeError:
            return
        symbols = parser.parse(source)
        self._format_symbols(symbols, lines, depth)

    def _format_symbols(
        self, symbols: list[Symbol], lines: list[str], depth: int
    ) -> None:
        indent = "  " * depth
        for sym in symbols:
            if sym.kind == "class":
                lines.append(f"{indent}class {sym.name}")
            elif sym.kind in ("function", "method"):
                # Name may contain full signature (e.g. from Python parser)
                lines.append(f"{indent}{sym.name}")
            else:
                lines.append(f"{indent}{sym.kind} {sym.name}")
            if sym.children:
                self._format_symbols(sym.children, lines, depth + 1)

    def _parse_python(self, path: Path, lines: list[str], depth: int) -> None:
        try:
            source = path.read_text()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            return

        indent = "  " * depth
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                lines.append(f"{indent}class {node.name}")
                self._parse_class_body(node, lines, depth + 1)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sig = self._format_function(node)
                lines.append(f"{indent}{sig}")

    def _parse_class_body(
        self, cls: ast.ClassDef, lines: list[str], depth: int
    ) -> None:
        indent = "  " * depth
        for node in ast.iter_child_nodes(cls):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sig = self._format_function(node)
                lines.append(f"{indent}{sig}")

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


class RepoMapTool(BaseTool):
    """Tool that generates a structural overview of the codebase."""

    name = "repo_map"
    description = (
        "Generate a structural map of the repository showing files, classes, "
        "and function signatures. Use this to understand the codebase layout "
        "before making changes."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to map. Defaults to workspace root.",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum directory depth to traverse.",
            },
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], env: Environment | None) -> ToolResult:
        if env is None:
            return ToolResult(output="", error="No environment available")
        path = args.get("path", ".")
        max_depth = args.get("max_depth")
        # Resolve relative to environment workdir
        if hasattr(env, "workdir"):
            base = Path(env.workdir) / path
        else:
            base = Path(path)
        if not base.is_dir():
            return ToolResult(output="", error=f"Not a directory: {path}")
        rm = RepoMap(str(base), max_depth=max_depth)
        output = rm.generate()
        return ToolResult(output=output)

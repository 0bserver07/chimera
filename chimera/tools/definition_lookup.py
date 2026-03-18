"""Symbol definition lookup across a codebase.

Find where functions, classes, methods, and variables are defined using
AST analysis for Python and regex patterns for other languages.  Reuses
Chimera's existing language parsers where appropriate.
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from typing import Any

from chimera.core.tool import BaseTool
from chimera.env.base import Environment
from chimera.types import ToolResult

# Directories to skip when walking the file tree.
_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules", ".venv", "venv", ".tox", ".eggs",
    "dist", "build", ".chimera_checkpoints",
}

# File extensions we know how to search for definitions.
_SOURCE_EXTENSIONS = frozenset((
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".go", ".rs", ".java", ".rb",
    ".c", ".cpp", ".h", ".hpp",
))


@dataclass
class Definition:
    """A found symbol definition."""

    symbol: str
    kind: str          # "function", "class", "method", "variable", "struct", etc.
    file: str
    line: int
    source: str        # the source code of the definition


class DefinitionFinder:
    """Find symbol definitions across a codebase.

    Uses AST for Python, regex patterns for other languages.
    """

    def __init__(self, workdir: str) -> None:
        self._workdir = workdir

    def find(self, symbol: str, file_hint: str | None = None) -> list[Definition]:
        """Find all definitions of a symbol.

        Args:
            symbol: Name to search for (function, class, variable).
            file_hint: Optional file to search first.

        Returns:
            List of definitions found, ordered by relevance.
        """
        results: list[Definition] = []

        # Search hint file first
        if file_hint:
            full_path = os.path.join(self._workdir, file_hint)
            if os.path.isfile(full_path):
                results.extend(self._search_file(full_path, file_hint, symbol))

        # Then search all files
        for root, dirs, files in os.walk(self._workdir):
            # Skip common non-source directories
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]
            for fname in sorted(files):
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, self._workdir)
                if rel_path == file_hint:
                    continue  # already searched
                if not self._is_source_file(fname):
                    continue
                results.extend(self._search_file(fpath, rel_path, symbol))

        return results

    def _is_source_file(self, fname: str) -> bool:
        """Return True if *fname* has a recognised source extension."""
        _, ext = os.path.splitext(fname)
        return ext in _SOURCE_EXTENSIONS

    def _search_file(
        self, full_path: str, rel_path: str, symbol: str,
    ) -> list[Definition]:
        """Search a single file for definitions of *symbol*."""
        try:
            with open(full_path, encoding="utf-8", errors="replace") as f:
                source = f.read()
        except OSError:
            return []

        if full_path.endswith(".py"):
            return self._search_python(source, rel_path, symbol)
        return self._search_regex(source, rel_path, symbol)

    # ------------------------------------------------------------------
    # Python: AST-based search
    # ------------------------------------------------------------------

    def _search_python(
        self, source: str, rel_path: str, symbol: str,
    ) -> list[Definition]:
        """Use AST for Python files."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return self._search_regex(source, rel_path, symbol)

        results: list[Definition] = []
        lines = source.splitlines()

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                end = node.end_lineno or node.lineno
                src = "\n".join(lines[node.lineno - 1 : end])
                kind = "method" if self._is_inside_class(tree, node) else "function"
                results.append(Definition(
                    symbol=symbol, kind=kind, file=rel_path,
                    line=node.lineno, source=src,
                ))
            elif isinstance(node, ast.ClassDef) and node.name == symbol:
                # Just the class line + docstring, not full body
                src = lines[node.lineno - 1]
                results.append(Definition(
                    symbol=symbol, kind="class", file=rel_path,
                    line=node.lineno, source=src,
                ))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == symbol:
                        src = lines[node.lineno - 1]
                        results.append(Definition(
                            symbol=symbol, kind="variable", file=rel_path,
                            line=node.lineno, source=src,
                        ))

        return results

    def _is_inside_class(self, tree: ast.AST, func_node: ast.AST) -> bool:
        """Check if a function node is directly inside a class body."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for child in ast.iter_child_nodes(node):
                    if child is func_node:
                        return True
        return False

    # ------------------------------------------------------------------
    # Other languages: regex-based search
    # ------------------------------------------------------------------

    # Each pattern is (regex_template, kind).  ``{sym}`` is replaced with
    # the escaped symbol name before matching.
    _REGEX_PATTERNS: list[tuple[str, str]] = [
        # JS / TS functions
        (r"(?:export\s+)?(?:async\s+)?function\s+{sym}\s*\(", "function"),
        # JS / TS classes
        (r"(?:export\s+)?(?:abstract\s+)?class\s+{sym}[\s{{(]", "class"),
        # JS / TS interfaces
        (r"(?:export\s+)?interface\s+{sym}[\s{{]", "interface"),
        # JS / TS const-arrow / const-function
        (r"(?:export\s+)?(?:const|let|var)\s+{sym}\s*[=:]", "variable"),
        # Go functions
        (r"func\s+{sym}\s*\(", "function"),
        # Go methods (receiver in parens before name)
        (r"func\s+\([^)]*\)\s+{sym}\s*\(", "method"),
        # Go struct / interface
        (r"type\s+{sym}\s+struct\b", "struct"),
        (r"type\s+{sym}\s+interface\b", "interface"),
        # Rust
        (r"(?:pub\s+)?fn\s+{sym}\s*[\(<]", "function"),
        (r"(?:pub\s+)?struct\s+{sym}[\s{{]", "struct"),
        (r"(?:pub\s+)?trait\s+{sym}[\s{{]", "trait"),
        (r"(?:pub\s+)?enum\s+{sym}[\s{{]", "enum"),
        # Java / C / C++ / C#
        (r"class\s+{sym}[\s{{:(]", "class"),
        # Ruby
        (r"def\s+{sym}[\s(]", "function"),
        (r"class\s+{sym}[\s<]", "class"),
        (r"module\s+{sym}\b", "module"),
    ]

    def _search_regex(
        self, source: str, rel_path: str, symbol: str,
    ) -> list[Definition]:
        """Regex fallback for non-Python files."""
        results: list[Definition] = []
        escaped = re.escape(symbol)
        compiled = [
            (re.compile(pat.replace("{sym}", escaped)), kind)
            for pat, kind in self._REGEX_PATTERNS
        ]

        for line_num, line in enumerate(source.splitlines(), 1):
            for regex, kind in compiled:
                if regex.search(line):
                    results.append(Definition(
                        symbol=symbol, kind=kind, file=rel_path,
                        line=line_num, source=line.strip(),
                    ))
                    break  # one match per line is enough

        return results


class DefinitionLookupTool(BaseTool):
    """Find where a symbol is defined in the codebase."""

    name = "definition_lookup"
    description = (
        "Find where a function, class, method, or variable is defined. "
        "Returns file path, line number, and source code."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "The symbol name to find (function, class, variable).",
            },
            "file_hint": {
                "type": "string",
                "description": "Optional file to search first.",
            },
        },
        "required": ["symbol"],
    }

    def execute(self, args: dict[str, Any], env: Environment | None = None) -> ToolResult:
        """Execute the definition lookup.

        Args:
            args: Must contain ``symbol``; may contain ``file_hint``.
            env: Execution environment whose ``workdir`` is used as search
                root.  Falls back to ``"."`` when *env* is ``None``.

        Returns:
            A ToolResult listing found definitions or an appropriate message.
        """
        workdir = getattr(env, "workdir", ".") if env else "."
        finder = DefinitionFinder(str(workdir))

        symbol = args["symbol"]
        file_hint = args.get("file_hint")

        definitions = finder.find(symbol, file_hint)

        if not definitions:
            return ToolResult(output=f"No definition found for '{symbol}'")

        lines = [f"Found {len(definitions)} definition(s) for '{symbol}':\n"]
        for d in definitions[:10]:  # limit output to 10
            lines.append(f"  {d.file}:{d.line} ({d.kind})")
            lines.append(f"    {d.source[:200]}")
            lines.append("")

        return ToolResult(
            output="\n".join(lines),
            metadata={"count": len(definitions)},
        )

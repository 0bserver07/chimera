"""Regex-based TypeScript/JavaScript parser."""
from __future__ import annotations

import re

from chimera.tools.parsers.base import LanguageParser, Symbol

_CLASS_RE = re.compile(
    r"^(?:export\s+)?(?:abstract\s+)?(class|interface)\s+(\w+)", re.MULTILINE
)
_FUNCTION_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE
)
_CONST_FN_RE = re.compile(
    r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)\s*=>|function\s*\()",
    re.MULTILINE,
)
_METHOD_RE = re.compile(r"^\s{2,}(?:async\s+)?(\w+)\s*\(", re.MULTILINE)


class TypeScriptParser(LanguageParser):
    """Parse TypeScript/JavaScript source using regex patterns."""

    extensions = (".ts", ".tsx", ".js", ".jsx")

    def parse(self, source: str) -> list[Symbol]:
        """Parse TypeScript/JavaScript source and extract symbols.

        Args:
            source: TypeScript or JavaScript source code text.

        Returns:
            List of top-level symbols with nested children.
        """
        if not source.strip():
            return []

        symbols: list[Symbol] = []
        lines = source.splitlines()

        # Track class body ranges to find methods
        class_ranges: list[tuple[int, int, str, str]] = []  # (start, end, name, kind)
        for m in _CLASS_RE.finditer(source):
            kind = "interface" if m.group(1) == "interface" else "class"
            name = m.group(2)
            # Find class body by counting braces
            start_pos = m.end()
            brace_start = source.find("{", start_pos)
            if brace_start == -1:
                continue
            depth = 0
            end_pos = brace_start
            for i, ch in enumerate(source[brace_start:], brace_start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end_pos = i
                        break
            start_line = source[:m.start()].count("\n")
            end_line = source[:end_pos].count("\n")
            class_ranges.append((start_line, end_line, name, kind))

        # Build set of line numbers that are inside class bodies
        class_line_sets: dict[int, tuple[str, str]] = {}  # line -> (name, kind)
        for start, end, name, kind in class_ranges:
            for ln in range(start, end + 1):
                class_line_sets[ln] = (name, kind)

        # Process classes
        seen_classes: dict[str, Symbol] = {}
        for start, end, name, kind in class_ranges:
            sym = Symbol(name=name, kind=kind)
            seen_classes[name] = sym
            symbols.append(sym)
            # Find methods inside this class
            body_text_start = source.find("{", source.find(f"class {name}" if kind == "class" else f"interface {name}"))
            if body_text_start == -1:
                body_text_start = source.find("{", source.find(name))
            body_lines = lines[start + 1:end]
            for line in body_lines:
                mm = _METHOD_RE.match(line)
                if mm:
                    method_name = mm.group(1)
                    if method_name not in ("if", "for", "while", "switch", "catch"):
                        sym.children.append(Symbol(name=method_name, kind="method"))

        # Process top-level functions (not inside classes)
        for m in _FUNCTION_RE.finditer(source):
            line_no = source[:m.start()].count("\n")
            if line_no not in class_line_sets:
                symbols.append(Symbol(name=m.group(1), kind="function"))

        # Process const arrow functions / const function expressions
        for m in _CONST_FN_RE.finditer(source):
            line_no = source[:m.start()].count("\n")
            if line_no not in class_line_sets:
                symbols.append(Symbol(name=m.group(1), kind="function"))

        return symbols

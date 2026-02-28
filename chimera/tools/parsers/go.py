"""Regex-based Go language parser."""
from __future__ import annotations

import re

from chimera.tools.parsers.base import LanguageParser, Symbol

_FUNC_RE = re.compile(r"^func\s+(\w+)\s*\(", re.MULTILINE)
_METHOD_RE = re.compile(r"^func\s+\([^)]+\)\s+(\w+)\s*\(", re.MULTILINE)
_STRUCT_RE = re.compile(r"^type\s+(\w+)\s+struct\b", re.MULTILINE)
_INTERFACE_RE = re.compile(r"^type\s+(\w+)\s+interface\b", re.MULTILINE)


class GoParser(LanguageParser):
    """Parse Go source using regex patterns."""

    extensions = (".go",)

    def parse(self, source: str) -> list[Symbol]:
        """Parse Go source and extract symbols.

        Args:
            source: Go source code text.

        Returns:
            List of top-level symbols with nested children.
        """
        if not source.strip():
            return []

        symbols: list[Symbol] = []

        for m in _METHOD_RE.finditer(source):
            symbols.append(Symbol(name=m.group(1), kind="method"))

        method_positions = {m.start() for m in _METHOD_RE.finditer(source)}

        for m in _FUNC_RE.finditer(source):
            if m.start() not in method_positions:
                symbols.append(Symbol(name=m.group(1), kind="function"))

        for m in _STRUCT_RE.finditer(source):
            symbols.append(Symbol(name=m.group(1), kind="struct"))

        for m in _INTERFACE_RE.finditer(source):
            symbols.append(Symbol(name=m.group(1), kind="interface"))

        return symbols

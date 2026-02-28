"""Regex-based Rust language parser."""
from __future__ import annotations

import re

from chimera.tools.parsers.base import LanguageParser, Symbol

_FN_RE = re.compile(r"^(?:pub(?:\s*\([^)]*\))?\s+)?fn\s+(\w+)\s*[<(]", re.MULTILINE)
_STRUCT_RE = re.compile(r"^(?:pub(?:\s*\([^)]*\))?\s+)?struct\s+(\w+)", re.MULTILINE)
_TRAIT_RE = re.compile(r"^(?:pub(?:\s*\([^)]*\))?\s+)?trait\s+(\w+)", re.MULTILINE)
_IMPL_RE = re.compile(r"^impl(?:\s*<[^>]*>)?\s+(\w+)", re.MULTILINE)
_METHOD_IN_IMPL_RE = re.compile(
    r"^\s{4}(?:pub(?:\s*\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]", re.MULTILINE
)


class RustParser(LanguageParser):
    """Parse Rust source using regex patterns."""

    extensions = (".rs",)

    def parse(self, source: str) -> list[Symbol]:
        """Parse Rust source and extract symbols.

        Args:
            source: Rust source code text.

        Returns:
            List of top-level symbols with nested children.
        """
        if not source.strip():
            return []

        symbols: list[Symbol] = []

        # Track impl block positions to extract methods
        impl_ranges: list[tuple[int, int, str]] = []
        for m in _IMPL_RE.finditer(source):
            name = m.group(1)
            brace_start = source.find("{", m.end())
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
            impl_ranges.append((m.start(), end_pos, name))

        # Build set of positions inside impl blocks
        impl_positions: set[int] = set()
        for start, end, _ in impl_ranges:
            impl_positions.update(range(start, end + 1))

        # Add impl symbols with methods
        for start, end, name in impl_ranges:
            impl_sym = Symbol(name=name, kind="impl")
            body = source[start:end + 1]
            for mm in _METHOD_IN_IMPL_RE.finditer(body):
                impl_sym.children.append(Symbol(name=mm.group(1), kind="method"))
            symbols.append(impl_sym)

        # Top-level functions (not inside impl blocks)
        for m in _FN_RE.finditer(source):
            if m.start() not in impl_positions:
                symbols.append(Symbol(name=m.group(1), kind="function"))

        # Structs
        for m in _STRUCT_RE.finditer(source):
            symbols.append(Symbol(name=m.group(1), kind="struct"))

        # Traits
        for m in _TRAIT_RE.finditer(source):
            symbols.append(Symbol(name=m.group(1), kind="trait"))

        return symbols

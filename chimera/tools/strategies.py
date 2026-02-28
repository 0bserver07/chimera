"""Fuzzy string matching strategies for the edit tool."""
from __future__ import annotations

import difflib
import re
from abc import ABC, abstractmethod


class MatchResult:
    """Result of a fuzzy match attempt."""

    __slots__ = ("start", "end", "strategy_name")

    def __init__(self, start: int, end: int, strategy_name: str) -> None:
        self.start = start
        self.end = end
        self.strategy_name = strategy_name


class EditStrategy(ABC):
    """Base class for edit matching strategies."""

    name: str = ""

    @abstractmethod
    def find(self, content: str, search: str) -> MatchResult | None:
        """Find *search* in *content*.

        Returns:
            A MatchResult with (start, end) positions in the original
            content, or None if no match.
        """


class ExactMatch(EditStrategy):
    """Character-for-character exact match."""

    name = "exact"

    def find(self, content: str, search: str) -> MatchResult | None:
        idx = content.find(search)
        if idx == -1:
            return None
        # Check uniqueness
        if content.find(search, idx + 1) != -1:
            return None  # ambiguous
        return MatchResult(idx, idx + len(search), self.name)


class StripLines(EditStrategy):
    """Strip leading/trailing whitespace per line before comparing."""

    name = "strip_lines"

    def find(self, content: str, search: str) -> MatchResult | None:
        stripped_search = "\n".join(line.strip() for line in search.split("\n"))
        lines = content.split("\n")
        search_lines = stripped_search.split("\n")
        n = len(search_lines)
        for i in range(len(lines) - n + 1):
            window = [lines[j].strip() for j in range(i, i + n)]
            if window == search_lines:
                start = sum(len(lines[j]) + 1 for j in range(i))
                end = sum(len(lines[j]) + 1 for j in range(i + n)) - 1
                return MatchResult(start, end, self.name)
        return None


class NormalizeWhitespace(EditStrategy):
    """Collapse runs of whitespace to single space before comparing."""

    name = "normalize_whitespace"

    def find(self, content: str, search: str) -> MatchResult | None:
        norm_search = re.sub(r"\s+", " ", search.strip())
        lines = content.split("\n")
        search_lines = search.strip().split("\n")
        n = len(search_lines)
        for i in range(len(lines) - n + 1):
            window = "\n".join(lines[i : i + n])
            norm_window = re.sub(r"\s+", " ", window.strip())
            if norm_window == norm_search:
                start = sum(len(lines[j]) + 1 for j in range(i))
                end = sum(len(lines[j]) + 1 for j in range(i + n)) - 1
                return MatchResult(start, end, self.name)
        return None


class IndentFlexible(EditStrategy):
    """Normalize indentation to relative levels before comparing."""

    name = "indent_flexible"

    def find(self, content: str, search: str) -> MatchResult | None:
        def relative_indent(text: str) -> list[tuple[int, str]]:
            lines = text.split("\n")
            result = []
            base = None
            for line in lines:
                stripped = line.lstrip()
                if not stripped:
                    result.append((0, ""))
                    continue
                indent = len(line) - len(stripped)
                if base is None:
                    base = indent
                result.append((indent - base, stripped))
            return result

        search_rel = relative_indent(search)
        lines = content.split("\n")
        n = len(search_rel)
        for i in range(len(lines) - n + 1):
            window = "\n".join(lines[i : i + n])
            window_rel = relative_indent(window)
            if len(window_rel) == len(search_rel):
                match = all(
                    wr[0] == sr[0] and wr[1] == sr[1]
                    for wr, sr in zip(window_rel, search_rel)
                )
                if match:
                    start = sum(len(lines[j]) + 1 for j in range(i))
                    end = sum(len(lines[j]) + 1 for j in range(i + n)) - 1
                    return MatchResult(start, end, self.name)
        return None


class LevenshteinMatch(EditStrategy):
    """Fuzzy match using SequenceMatcher with a similarity threshold."""

    name = "levenshtein"

    def __init__(self, threshold: float = 0.85) -> None:
        self.threshold = threshold

    def find(self, content: str, search: str) -> MatchResult | None:
        lines = content.split("\n")
        search_lines = search.split("\n")
        n = len(search_lines)
        best_ratio = 0.0
        best_start = -1
        best_end = -1
        for i in range(len(lines) - n + 1):
            window = "\n".join(lines[i : i + n])
            ratio = difflib.SequenceMatcher(None, window, search).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = sum(len(lines[j]) + 1 for j in range(i))
                best_end = sum(len(lines[j]) + 1 for j in range(i + n)) - 1
        if best_ratio >= self.threshold and best_start >= 0:
            return MatchResult(best_start, best_end, self.name)
        return None


# Default strategy chain
DEFAULT_STRATEGIES: list[EditStrategy] = [
    ExactMatch(),
    StripLines(),
    NormalizeWhitespace(),
    IndentFlexible(),
    LevenshteinMatch(),
]


class FuzzyEditor:
    """Tries strategies in order, returns first match.

    Args:
        strategies: Ordered list of strategies to try. Defaults to all 5.
    """

    def __init__(self, strategies: list[EditStrategy] | None = None) -> None:
        self.strategies = strategies or list(DEFAULT_STRATEGIES)

    def find(self, content: str, search: str) -> MatchResult | None:
        """Try each strategy in order, return first match."""
        for strategy in self.strategies:
            result = strategy.find(content, search)
            if result is not None:
                return result
        return None

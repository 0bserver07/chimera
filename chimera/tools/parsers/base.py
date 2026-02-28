"""Base language parser for repository mapping."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Symbol:
    """A code symbol extracted from source."""

    name: str
    kind: str  # "class", "function", "method", "interface", "struct", "trait", "impl"
    children: list[Symbol] = field(default_factory=list)


class LanguageParser(ABC):
    """Abstract base for language-specific source parsers."""

    extensions: tuple[str, ...] = ()

    @abstractmethod
    def parse(self, source: str) -> list[Symbol]:
        """Parse source code and extract symbols.

        Args:
            source: Source code text.

        Returns:
            List of top-level symbols with nested children.
        """

"""Token-budget-aware context selection.

The :class:`FocusChain` ranks context items by relevance and selects the
most important ones that fit within a configurable token budget, avoiding
the cost of sending everything to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.env.base import Environment


@dataclass
class ContextItem:
    """A piece of context with relevance scoring.

    Attributes:
        content: The raw text content.
        source: Origin label, e.g. ``"file:utils.py"`` or ``"history:turn_3"``.
        tokens: Estimated token count (``len(content) // 4``).
        relevance: Relevance score from 0.0 (lowest) to 1.0 (highest).
    """

    content: str
    source: str
    tokens: int
    relevance: float


class FocusChain:
    """Select the most relevant context to fit within a token budget.

    Instead of sending everything to the LLM, ranks context items by
    relevance and selects the most important ones that fit the budget.

    Args:
        token_budget: Maximum number of tokens the selected context may
            consume.  Defaults to 4000.
    """

    def __init__(self, token_budget: int = 4000) -> None:
        self._budget = token_budget
        self._items: list[ContextItem] = []

    @property
    def budget(self) -> int:
        """The configured token budget."""
        return self._budget

    @property
    def items(self) -> list[ContextItem]:
        """A copy of all registered context items."""
        return list(self._items)

    def add(self, content: str, source: str, relevance: float = 0.5) -> None:
        """Add a context item with estimated token count.

        Args:
            content: Raw text to include as context.
            source: Label describing the origin of this content.
            relevance: Score between 0.0 and 1.0 indicating importance.

        Raises:
            ValueError: If *relevance* is outside the closed interval
                ``[0.0, 1.0]`` or is NaN. The docstring has always
                advertised this range; we now enforce it so callers
                don't silently inject bogus scores that break the
                downstream ranking in :meth:`select`.
        """
        # NaN compares False with every inequality, so this catches
        # both out-of-range and NaN in one check.
        if not (0.0 <= relevance <= 1.0):
            raise ValueError(
                f"relevance must be in [0.0, 1.0], got {relevance!r}"
            )
        tokens = len(content) // 4  # rough estimate
        self._items.append(
            ContextItem(content=content, source=source, tokens=tokens, relevance=relevance)
        )

    def add_file(self, path: str, env: Environment, relevance: float = 0.5) -> None:
        """Add a file's content as a context item.

        Args:
            path: Path to the file within the environment.
            env: Environment used to read the file.
            relevance: Score between 0.0 and 1.0 indicating importance.
        """
        try:
            content = env.read_file(path)
            self.add(content, source=f"file:{path}", relevance=relevance)
        except (FileNotFoundError, OSError):
            pass  # skip files that can't be read

    def add_files(self, paths: list[str], env: Environment, relevance: float = 0.5) -> None:
        """Add multiple files.

        Args:
            paths: List of file paths within the environment.
            env: Environment used to read the files.
            relevance: Score between 0.0 and 1.0 indicating importance.
        """
        for path in paths:
            self.add_file(path, env, relevance)

    def select(self) -> list[ContextItem]:
        """Select items that fit within token budget, highest relevance first.

        Returns:
            A list of :class:`ContextItem` objects sorted by descending
            relevance, whose cumulative token count does not exceed
            :attr:`budget`.
        """
        sorted_items = sorted(self._items, key=lambda x: x.relevance, reverse=True)
        selected: list[ContextItem] = []
        remaining = self._budget
        for item in sorted_items:
            if item.tokens <= remaining:
                selected.append(item)
                remaining -= item.tokens
        return selected

    def to_prompt_section(self) -> str:
        """Render selected context as a prompt section.

        Returns:
            A Markdown-formatted string with each selected item under its
            own heading, or an empty string when nothing is selected.
        """
        selected = self.select()
        if not selected:
            return ""
        lines = ["## Context\n"]
        for item in selected:
            lines.append(f"### {item.source}\n")
            lines.append(item.content)
            lines.append("")
        return "\n".join(lines)

    def clear(self) -> None:
        """Remove all context items."""
        self._items.clear()

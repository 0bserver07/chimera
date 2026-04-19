"""Memory Consolidation: two-phase learning pipeline.

Ported from Codex's memory consolidation approach.

Phase 1 (Explore): Collect raw facts, observations, and code snippets from
conversation history and tool outputs.

Phase 2 (Consolidate): Merge, deduplicate, and structure the raw facts into
a compact, queryable memory store.

Example::

    consolidator = MemoryConsolidator()
    consolidator.add_fact("The project uses pytest for testing.")
    consolidator.add_fact("Tests are in tests/ directory.")
    consolidated = consolidator.consolidate()
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.types import Message


@dataclass
class Fact:
    """A single fact extracted during exploration.

    Args:
        content: The fact text.
        source: Where the fact came from (e.g. tool name, file path).
        category: Optional category tag (e.g. "architecture", "testing").
        confidence: 0.0-1.0 confidence score.
    """

    content: str
    source: str = ""
    category: str = ""
    confidence: float = 1.0


@dataclass
class ConsolidatedMemory:
    """Structured memory produced by the consolidation phase.

    Args:
        facts: Deduplicated, categorized facts.
        categories: Mapping of category name to list of facts.
        summary: A compact text summary of all knowledge.
    """

    facts: list[Fact] = field(default_factory=list)
    categories: dict[str, list[Fact]] = field(default_factory=dict)
    summary: str = ""

    def query(self, keyword: str) -> list[Fact]:
        """Return facts whose content contains *keyword* (case-insensitive)."""
        kw = keyword.lower()
        return [f for f in self.facts if kw in f.content.lower()]

    def by_category(self, category: str) -> list[Fact]:
        """Return all facts in a category."""
        return self.categories.get(category, [])


class MemoryConsolidator:
    """Two-phase memory pipeline: explore then consolidate.

    Phase 1: Call :meth:`add_fact` or :meth:`extract_from_messages` to
    collect raw facts.

    Phase 2: Call :meth:`consolidate` to deduplicate and structure.
    """

    def __init__(self) -> None:
        self._raw_facts: list[Fact] = []

    @property
    def raw_facts(self) -> list[Fact]:
        """All facts collected so far (Phase 1 output)."""
        return list(self._raw_facts)

    def add_fact(
        self,
        content: str,
        source: str = "",
        category: str = "",
        confidence: float = 1.0,
    ) -> None:
        """Add a single fact during the exploration phase.

        Args:
            content: The fact text.
            source: Origin of the fact.
            category: Optional category tag.
            confidence: Confidence level (0.0-1.0).
        """
        self._raw_facts.append(Fact(
            content=content.strip(),
            source=source,
            category=category,
            confidence=confidence,
        ))

    def extract_from_messages(
        self,
        messages: list["Message"],
        source: str = "conversation",
    ) -> int:
        """Extract facts from conversation messages (Phase 1 helper).

        Scans assistant and tool messages for declarative sentences
        (heuristic: lines containing "is", "uses", "has", "are", "runs").

        Args:
            messages: Conversation history to scan.
            source: Source label for extracted facts.

        Returns:
            Number of facts extracted.
        """
        count = 0
        indicator = re.compile(
            r"\b(is|uses|has|are|runs|requires|supports|contains|provides)\b",
            re.IGNORECASE,
        )
        for msg in messages:
            if msg.role not in ("assistant", "tool"):
                continue
            for line in msg.content.splitlines():
                line = line.strip()
                if len(line) < 10 or len(line) > 200:
                    continue
                if indicator.search(line):
                    self.add_fact(line, source=source)
                    count += 1
        return count

    def consolidate(self) -> ConsolidatedMemory:
        """Phase 2: Deduplicate and structure collected facts.

        This method does *not* mutate the facts stored in
        :attr:`raw_facts` (or any :class:`Fact` objects passed into the
        consolidator). New :class:`Fact` instances are constructed for
        the returned :class:`ConsolidatedMemory` whenever a field (e.g.
        ``category``) needs to change.

        Returns:
            A ConsolidatedMemory with categorized, deduplicated facts
            and a text summary.
        """
        # Deduplicate by normalized content. We defensively copy each
        # fact so that downstream category assignment can never mutate
        # the caller-visible Fact objects.
        seen: dict[str, Fact] = {}
        for original in self._raw_facts:
            fact = copy.copy(original)
            key = fact.content.lower().strip()
            if key in seen:
                # Keep higher confidence version
                if fact.confidence > seen[key].confidence:
                    seen[key] = fact
            else:
                seen[key] = fact

        # Auto-categorize uncategorized facts by constructing a new Fact
        # via dataclasses.replace so the copies above are also not
        # retroactively mutated — the returned list is wholly new.
        unique_facts: list[Fact] = []
        for fact in seen.values():
            if not fact.category:
                fact = replace(fact, category=_auto_categorize(fact.content))
            unique_facts.append(fact)

        # Group by category
        categories: dict[str, list[Fact]] = {}
        for fact in unique_facts:
            cat = fact.category or "general"
            categories.setdefault(cat, []).append(fact)

        # Build summary
        summary_parts = []
        for cat, facts in sorted(categories.items()):
            summary_parts.append(f"[{cat}]")
            for f in facts:
                summary_parts.append(f"  - {f.content}")

        return ConsolidatedMemory(
            facts=unique_facts,
            categories=categories,
            summary="\n".join(summary_parts),
        )

    def clear(self) -> None:
        """Clear all collected facts."""
        self._raw_facts.clear()


def _auto_categorize(content: str) -> str:
    """Simple keyword-based auto-categorization."""
    lower = content.lower()
    if any(w in lower for w in ("test", "pytest", "unittest", "spec")):
        return "testing"
    if any(w in lower for w in ("import", "dependency", "package", "install")):
        return "dependencies"
    if any(w in lower for w in ("api", "endpoint", "route", "http", "rest")):
        return "api"
    if any(w in lower for w in ("database", "sql", "table", "schema", "model")):
        return "data"
    if any(w in lower for w in ("config", "setting", "environment", "env")):
        return "config"
    if any(w in lower for w in ("architecture", "layer", "module", "pattern")):
        return "architecture"
    return "general"

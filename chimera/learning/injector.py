"""LearningInjector: inject relevant learned patterns before each agent turn."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.types import Message

from chimera.learning.observation import CATEGORY_THRESHOLDS
from chimera.learning.store import LearningStore

# Short words to drop from FTS queries (common English stop words).
_STOP_WORDS = frozenset(
    "a an and are as at be but by for from got has have he her his i if in "
    "into is it its me my no not of on or our out she so than that the their "
    "them then there these they this to up us was we were what when which who "
    "will with you your".split()
)

__all__ = ["LearningInjector"]


class LearningInjector:
    """Inject relevant learned patterns before each agent turn.

    Queries the learning store with recent error/task context and returns
    formatted strings for injection into the agent prompt. Only injects
    observations that meet or exceed the category-specific confidence
    threshold.

    Args:
        store: The learning store to query.
        max_injections: Maximum number of observations to inject per turn.
    """

    def __init__(self, store: LearningStore, max_injections: int = 3) -> None:
        self._store = store
        self._max_injections = max_injections

    def get_injections(
        self,
        context: list[Message],
        project_path: str = "",
    ) -> list[str]:
        """Get relevant learned observations for injection.

        Extracts recent error context from conversation history, queries
        the store, and filters by category-specific confidence thresholds.

        Args:
            context: Current conversation history.
            project_path: Current project path for scoped queries.

        Returns:
            List of formatted injection strings, up to ``max_injections``.
        """
        # Extract search terms from recent messages
        search_text = self._extract_context(context)
        if not search_text:
            return []

        # Query store for relevant observations
        results = self._store.query(
            search_text,
            project_path=project_path if project_path else None,
            limit=self._max_injections * 2,  # fetch extra, filter below
        )

        injections: list[str] = []
        for obs in results:
            # Check category-specific threshold
            threshold = CATEGORY_THRESHOLDS.get(obs.category, 0.5)
            if obs.confidence < threshold:
                continue
            injections.append(self._format_observation(obs))
            if len(injections) >= self._max_injections:
                break

        return injections

    @staticmethod
    def _extract_context(context: list[Message]) -> str:
        """Extract search terms from recent conversation messages.

        Builds an FTS5-compatible OR query from significant tokens in the
        last few messages.  Drops common stop words so the query focuses
        on domain-specific terms.

        Args:
            context: Conversation history.

        Returns:
            An FTS5 OR query string derived from recent context.
        """
        # Look at last 5 messages for relevant content
        recent = context[-5:] if len(context) > 5 else context
        raw_parts: list[str] = []
        for msg in recent:
            if msg.content:
                raw_parts.append(msg.content[:200])
        raw = " ".join(raw_parts)

        # Tokenize: split on non-alphanumeric (keep underscores)
        tokens = re.findall(r"[a-zA-Z0-9_]+", raw)
        # Filter: drop stop words and very short tokens
        significant = []
        for t in tokens:
            low = t.lower()
            if low not in _STOP_WORDS and len(low) >= 2:
                significant.append(low)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for tok in significant:
            if tok not in seen:
                seen.add(tok)
                unique.append(tok)
        # FTS5 OR query (up to 10 terms to keep it focused)
        return " OR ".join(unique[:10])

    @staticmethod
    def _format_observation(obs: object) -> str:
        """Format an observation for injection into the agent prompt.

        Args:
            obs: An Observation instance.

        Returns:
            Formatted string suitable for prompt injection.
        """
        # Use getattr to access fields (avoids import at method level)
        topic = getattr(obs, "topic", "")
        value = getattr(obs, "value", "")
        confidence = getattr(obs, "confidence", 0.0)
        category = getattr(obs, "category", None)
        cat_name = category.value if category else "unknown"
        return (
            f"[Learned/{cat_name}] {topic}: {value} "
            f"(confidence: {confidence:.0%})"
        )

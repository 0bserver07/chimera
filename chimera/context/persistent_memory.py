"""Persistent memory that survives session resets.

Extends :class:`~chimera.sessions.long_term_memory.LongTermMemory` and
:class:`~chimera.context.consolidation.MemoryConsolidator` to provide
automatic fact extraction and persistence across agent sessions.

On compaction or after every *N* turns, conversation history is scanned
for factual statements.  Extracted facts are stored in a JSON file and
re-injected as context when the next session starts.

Works with Claude Code's ``/compact`` and ``/clear`` commands via
EventBus integration (listens for ``compaction`` events).

Example::

    memory = PersistentMemory("~/.chimera/project_memory.json")

    # During a session, record turns
    memory.record_turn(messages)

    # On compaction, extract and persist
    memory.on_compaction(messages)

    # In a new session, load context
    context = memory.get_context_injection()
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chimera.context.consolidation import Fact, MemoryConsolidator
from chimera.sessions.long_term_memory import LongTermMemory, MemoryEntry

if TYPE_CHECKING:
    from chimera.events.base import EventBus
    from chimera.types import Message

__all__ = ["PersistentMemory", "PersistentMemoryConfig"]


@dataclass
class PersistentMemoryConfig:
    """Configuration for persistent memory.

    Args:
        path: Filesystem path for the JSON backing store.
        auto_save_interval: Extract and persist facts every N turns.
            Set to 0 to disable auto-save (only save on compaction).
        max_facts: Maximum number of facts to retain.
        categories: Categories of facts to include in context injection.
            ``None`` means all categories.
    """

    path: str = "~/.chimera/persistent_memory.json"
    auto_save_interval: int = 5
    max_facts: int = 200
    categories: list[str] | None = None


class PersistentMemory:
    """Memory that persists across session resets.

    Combines :class:`~chimera.sessions.long_term_memory.LongTermMemory`
    for key-value storage with
    :class:`~chimera.context.consolidation.MemoryConsolidator` for
    automatic fact extraction from conversations.

    Args:
        path: Filesystem path for the JSON backing store.
        auto_save_interval: Extract facts every N turns (0 to disable).
        max_facts: Maximum number of facts to retain.
        config: Optional :class:`PersistentMemoryConfig` (overrides
            individual arguments).
    """

    def __init__(
        self,
        path: str = "~/.chimera/persistent_memory.json",
        auto_save_interval: int = 5,
        max_facts: int = 200,
        config: PersistentMemoryConfig | None = None,
    ) -> None:
        if config is not None:
            path = config.path
            auto_save_interval = config.auto_save_interval
            max_facts = config.max_facts
            self._categories = config.categories
        else:
            self._categories = None

        self._ltm = LongTermMemory(path)
        self._consolidator = MemoryConsolidator()
        self._auto_save_interval = auto_save_interval
        self._max_facts = max_facts
        self._turn_count = 0
        self._pending_messages: list[Any] = []

    # -- Public API ----------------------------------------------------------

    def record_turn(self, messages: list[Message]) -> str | None:
        """Record messages from an agent turn.

        Accumulates messages for later extraction.  If the auto-save
        interval is reached, triggers extraction and persistence.

        Args:
            messages: Messages from the current turn.

        Returns:
            A nudge string if facts were extracted and saved, else
            ``None``.
        """
        self._pending_messages.extend(messages)
        self._turn_count += 1

        if (
            self._auto_save_interval > 0
            and self._turn_count % self._auto_save_interval == 0
        ):
            count = self._extract_and_save()
            if count > 0:
                return f"[memory] Extracted {count} facts from conversation."
        return None

    def on_compaction(self, messages: list[Message] | None = None) -> int:
        """Handle a compaction event.

        Extracts facts from all pending messages (and optionally from
        the provided messages) and persists them.

        Args:
            messages: Additional messages to extract from (e.g. the
                full conversation before compaction).

        Returns:
            Number of new facts extracted and stored.
        """
        if messages:
            self._pending_messages.extend(messages)
        return self._extract_and_save()

    def store_fact(
        self,
        key: str,
        content: str,
        category: str = "fact",
    ) -> None:
        """Manually store a fact.

        Args:
            key: Unique identifier for this fact.
            content: The fact content text.
            category: Category (default ``"fact"``).
        """
        self._ltm.store(key, content, category=category)

    def recall(self, key: str) -> str | None:
        """Recall a specific fact by key.

        Args:
            key: The fact key to look up.

        Returns:
            The stored content, or ``None``.
        """
        return self._ltm.recall(key)

    def search(self, query: str) -> list[MemoryEntry]:
        """Search facts by content.

        Args:
            query: Substring to search for.

        Returns:
            Matching memory entries.
        """
        return self._ltm.search(query)

    def get_context_injection(self) -> str:
        """Get a prompt section to inject into a new session.

        Renders all stored memories as a Markdown block suitable
        for system prompt injection.

        Returns:
            A Markdown string, or ``""`` if no memories exist.
        """
        return self._ltm.to_prompt_section(categories=self._categories)

    def clear(self) -> None:
        """Clear all stored memories and pending messages."""
        self._ltm.clear()
        self._consolidator.clear()
        self._pending_messages.clear()
        self._turn_count = 0

    @property
    def fact_count(self) -> int:
        """Number of stored facts."""
        return self._ltm.count

    @property
    def turn_count(self) -> int:
        """Number of recorded turns."""
        return self._turn_count

    @property
    def entries(self) -> list[MemoryEntry]:
        """All stored memory entries."""
        return self._ltm.entries

    # -- EventBus integration ------------------------------------------------

    def attach(self, event_bus: "EventBus") -> None:
        """Subscribe to compaction events on a Chimera EventBus.

        Args:
            event_bus: An :class:`~chimera.events.base.EventBus` instance.
        """
        event_bus.subscribe("compaction", self._handle_compaction_event)

    def _handle_compaction_event(self, event: Any) -> None:
        """Handle a CompactionEvent from the EventBus."""
        self.on_compaction()

    # -- Internals -----------------------------------------------------------

    def _extract_and_save(self) -> int:
        """Extract facts from pending messages and persist them.

        Returns:
            Number of new facts stored.
        """
        if not self._pending_messages:
            return 0

        # Extract facts using the consolidator
        self._consolidator.clear()
        count = self._consolidator.extract_from_messages(self._pending_messages)

        if count == 0:
            self._pending_messages.clear()
            return 0

        # Consolidate (deduplicate + categorize)
        consolidated = self._consolidator.consolidate()

        # Store in LTM
        stored = 0
        for fact in consolidated.facts:
            if self._ltm.count >= self._max_facts:
                break
            # Use a content-based key for deduplication
            key = f"auto_{_fact_key(fact.content)}"
            existing = self._ltm.recall(key)
            if existing is None:
                self._ltm.store(
                    key,
                    fact.content,
                    category=fact.category or "auto",
                )
                stored += 1

        self._pending_messages.clear()
        return stored


def _fact_key(content: str) -> str:
    """Generate a short key from fact content for deduplication."""
    import hashlib
    return hashlib.md5(content.lower().strip().encode()).hexdigest()[:12]

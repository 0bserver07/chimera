"""Persistent long-term memory across agent sessions.

Stores facts, learnings, preferences, and project context that the agent
can recall in future sessions.  Backed by a JSON file for simplicity;
the storage format is intentionally flat so it can be swapped for SQLite
later without changing the public API.

Inspired by Codex's memory consolidation system which extracts reusable
knowledge from rollouts and persists it for future sessions.  This module
provides the simpler, user-facing layer: explicit key-value memories with
categories, search, and prompt rendering.

Usage::

    memory = LongTermMemory("~/.chimera/memory.json")
    memory.store("user_name", "Alice", category="preference")
    memory.store("project_lang", "Python 3.12", category="project")

    # In a later session:
    memory = LongTermMemory("~/.chimera/memory.json")
    name = memory.recall("user_name")  # "Alice"
    project_context = memory.recall_category("project")
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["LongTermMemory", "MemoryEntry"]


@dataclass
class MemoryEntry:
    """A single memory entry."""

    key: str
    content: str
    category: str = "general"  # "fact", "preference", "learning", "project"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class LongTermMemory:
    """Persistent memory that survives across agent sessions.

    Stores facts, learnings, preferences, and project context that
    the agent can recall in future sessions.  Backed by a JSON file
    (simple) or SQLite (scalable).

    Inspired by Codex's memory consolidation system.

    Args:
        path: Filesystem path for the JSON backing store.  Shell
            expansion (``~``) is applied automatically.

    Usage::

        memory = LongTermMemory("~/.chimera/memory.json")
        memory.store("user_name", "Alice", category="preference")
        memory.store("project_lang", "Python 3.12", category="project")

        # In a later session:
        memory = LongTermMemory("~/.chimera/memory.json")
        name = memory.recall("user_name")  # "Alice"
        project_context = memory.recall_category("project")
    """

    def __init__(self, path: str) -> None:
        self._path = os.path.expanduser(path)
        self._entries: dict[str, MemoryEntry] = {}
        self._load()

    def store(
        self,
        key: str,
        content: str,
        category: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store or update a memory entry.

        When *key* already exists the ``created_at`` timestamp is
        preserved and only ``updated_at`` is refreshed.

        Args:
            key: Unique identifier for this memory.
            content: The memory content (free-form text).
            category: Logical grouping such as ``"fact"``,
                ``"preference"``, ``"learning"``, or ``"project"``.
            metadata: Optional arbitrary metadata dict.
        """
        now = time.time()
        existing = self._entries.get(key)
        self._entries[key] = MemoryEntry(
            key=key,
            content=content,
            category=category,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            metadata=metadata or {},
        )
        self._save()

    def recall(self, key: str) -> str | None:
        """Recall a specific memory by key.

        Args:
            key: The memory key to look up.

        Returns:
            The stored content string, or ``None`` if the key is absent.
        """
        entry = self._entries.get(key)
        return entry.content if entry else None

    def recall_category(self, category: str) -> list[MemoryEntry]:
        """Recall all memories in a category.

        Args:
            category: The category to filter by.

        Returns:
            List of matching :class:`MemoryEntry` objects.
        """
        return [e for e in self._entries.values() if e.category == category]

    def search(self, query: str) -> list[MemoryEntry]:
        """Search memories by content (case-insensitive substring).

        Matches against both the key and the content of each entry.

        Args:
            query: Substring to search for.

        Returns:
            List of matching :class:`MemoryEntry` objects.
        """
        query_lower = query.lower()
        return [
            e
            for e in self._entries.values()
            if query_lower in e.content.lower() or query_lower in e.key.lower()
        ]

    def forget(self, key: str) -> bool:
        """Remove a memory entry.

        Args:
            key: The memory key to remove.

        Returns:
            ``True`` if the key was found and removed, ``False``
            otherwise.
        """
        if key in self._entries:
            del self._entries[key]
            self._save()
            return True
        return False

    def clear(self) -> None:
        """Remove all memories."""
        self._entries.clear()
        self._save()

    @property
    def entries(self) -> list[MemoryEntry]:
        """All memory entries."""
        return list(self._entries.values())

    @property
    def count(self) -> int:
        """Number of stored memories."""
        return len(self._entries)

    def to_prompt_section(self, categories: list[str] | None = None) -> str:
        """Render memories as a prompt section for the agent.

        Produces a Markdown-formatted block suitable for injection into
        system prompts so the agent is aware of persisted knowledge.

        Args:
            categories: Filter to specific categories.  ``None`` means
                all categories are included.

        Returns:
            A Markdown string, or ``""`` if no memories match.
        """
        entries: list[MemoryEntry] | dict_values = self._entries.values()  # type: ignore[assignment]
        if categories:
            entries = [e for e in entries if e.category in categories]

        if not entries:
            return ""

        lines = ["## Agent Memory\n"]
        by_category: dict[str, list[MemoryEntry]] = {}
        for e in entries:
            by_category.setdefault(e.category, []).append(e)

        for cat, cat_entries in sorted(by_category.items()):
            lines.append(f"### {cat.title()}")
            for e in cat_entries:
                lines.append(f"- **{e.key}**: {e.content}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load from JSON file."""
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path) as f:
                data = json.load(f)
            for key, entry_data in data.items():
                self._entries[key] = MemoryEntry(
                    key=key,
                    content=entry_data["content"],
                    category=entry_data.get("category", "general"),
                    created_at=entry_data.get("created_at", 0),
                    updated_at=entry_data.get("updated_at", 0),
                    metadata=entry_data.get("metadata", {}),
                )
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # corrupted file, start fresh

    def _save(self) -> None:
        """Save to JSON file."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        data = {}
        for key, entry in self._entries.items():
            data[key] = {
                "content": entry.content,
                "category": entry.category,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "metadata": entry.metadata,
            }
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)

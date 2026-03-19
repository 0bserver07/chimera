"""Client-side context caching with hash-based deduplication.

Stores frequently-used context blocks (system prompts, file contents, tool
schemas) so they can be reused across turns without re-processing.

Complementary to provider-side prompt caching — this is client-side optimization.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CacheEntry:
    """A cached context block."""

    key: str
    content: str
    hash: str
    token_estimate: int
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    hit_count: int = 0


class ContextCache:
    """Client-side context cache with hash-based deduplication.

    Example::

        cache = ContextCache(max_entries=100)
        cache.put("system_prompt", long_system_prompt)
        cache.put("tools_schema", json.dumps(tools))

        # Later: check before re-processing
        if cached := cache.get("system_prompt"):
            system = cached.content
        else:
            system = build_system_prompt()  # expensive
            cache.put("system_prompt", system)
    """

    def __init__(self, max_entries: int = 200) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self._hash_index: dict[str, str] = {}  # hash → key (for dedup)
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0

    def put(self, key: str, content: str, token_estimate: int = 0) -> CacheEntry:
        """Store a context block.

        If identical content already exists under a different key, returns
        the existing entry (deduplication).

        Args:
            key: Cache key (e.g. "system_prompt", "file:src/main.py").
            content: The text content to cache.
            token_estimate: Approximate token count (0 = auto-estimate).

        Returns:
            The cache entry (new or existing).
        """
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        # Dedup: if same content exists under different key, link to it
        if content_hash in self._hash_index:
            existing_key = self._hash_index[content_hash]
            if existing_key in self._entries:
                entry = self._entries[existing_key]
                # Also register under the new key
                self._entries[key] = entry
                return entry

        if token_estimate == 0:
            token_estimate = len(content) // 4  # rough estimate

        entry = CacheEntry(
            key=key,
            content=content,
            hash=content_hash,
            token_estimate=token_estimate,
        )

        # Evict if at capacity (LRU)
        if len(self._entries) >= self._max_entries:
            self._evict_lru()

        self._entries[key] = entry
        self._hash_index[content_hash] = key
        return entry

    def get(self, key: str) -> CacheEntry | None:
        """Retrieve a cached entry.

        Args:
            key: Cache key.

        Returns:
            The entry, or None if not cached.
        """
        entry = self._entries.get(key)
        if entry is not None:
            entry.last_accessed = time.time()
            entry.hit_count += 1
            self._hits += 1
            return entry
        self._misses += 1
        return None

    def has(self, key: str) -> bool:
        """Check if a key exists in the cache."""
        return key in self._entries

    def invalidate(self, key: str) -> None:
        """Remove an entry from the cache."""
        entry = self._entries.pop(key, None)
        if entry:
            self._hash_index.pop(entry.hash, None)

    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()
        self._hash_index.clear()
        self._hits = 0
        self._misses = 0

    @property
    def size(self) -> int:
        """Number of cached entries."""
        return len(self._entries)

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (0.0–1.0)."""
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def stats(self) -> dict[str, Any]:
        """Cache statistics."""
        return {
            "entries": len(self._entries),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate,
            "total_tokens": sum(e.token_estimate for e in self._entries.values()),
        }

    def _evict_lru(self) -> None:
        """Evict the least-recently-used entry."""
        if not self._entries:
            return
        lru_key = min(self._entries, key=lambda k: self._entries[k].last_accessed)
        self.invalidate(lru_key)

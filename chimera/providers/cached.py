"""Response caching: SHA-based deduplication of LLM responses.

Wraps any Provider with an in-memory LRU cache. When the exact same prompt
is sent again, returns the cached response without an API call.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from chimera.providers.base import Provider, Response, StreamEvent, ToolSchema
from chimera.types import Message


@dataclass
class CacheStats:
    """Cache hit/miss statistics."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


def _cache_key(
    model: str,
    messages: list[Message],
    tools: list[ToolSchema] | None,
    temperature: float,
    thinking: Any = None,
) -> str:
    """Generate a SHA-256 cache key from request parameters.

    Includes ``thinking`` in the key so runs with and without extended
    reasoning are not confused for each other.
    """
    payload = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "tools": tools or [],
        "temperature": temperature,
        "thinking": str(getattr(thinking, "value", thinking)) if thinking is not None else None,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class CachedProvider(Provider):
    """Provider wrapper that caches responses.

    Example::

        base = create_provider(model="glm-5")
        cached = CachedProvider(base, max_entries=200)
        r1 = cached.complete(messages)  # API call
        r2 = cached.complete(messages)  # cache hit
    """

    def __init__(self, provider: Provider, max_entries: int = 100) -> None:
        self._provider = provider
        self._max_entries = max_entries
        self._cache: OrderedDict[str, Response] = OrderedDict()
        self._lock = threading.Lock()
        self._stats = CacheStats()

    @property
    def stats(self) -> CacheStats:
        """Cache statistics."""
        return self._stats

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> Response:
        key = _cache_key(
            self._provider.model_name, messages, tools, temperature, thinking,
        )

        with self._lock:
            if key in self._cache:
                self._stats.hits += 1
                self._cache.move_to_end(key)
                return self._cache[key]

        self._stats.misses += 1
        # Only forward ``thinking`` when non-None so we remain compatible with
        # custom Provider subclasses that predate the kwarg.
        if thinking is not None:
            response = self._provider.complete(
                messages, tools, temperature, max_tokens, thinking=thinking,
            )
        else:
            response = self._provider.complete(
                messages, tools, temperature, max_tokens,
            )

        with self._lock:
            self._cache[key] = response
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
                self._stats.evictions += 1

        return response

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> Iterator[StreamEvent]:
        # Streaming bypasses cache — delegate directly.
        if thinking is not None:
            return self._provider.stream(
                messages, tools, temperature, max_tokens, thinking=thinking,
            )
        return self._provider.stream(
            messages, tools, temperature, max_tokens,
        )

    def clear_cache(self) -> None:
        """Clear all cached responses."""
        with self._lock:
            self._cache.clear()

    @property
    def context_window(self) -> int:
        return self._provider.context_window

    @property
    def supports_tool_use(self) -> bool:
        return self._provider.supports_tool_use

    @property
    def model_name(self) -> str:
        return self._provider.model_name

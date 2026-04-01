"""Token counting and estimation utilities.

Provides :class:`TokenEstimator` with a fast heuristic estimator and
optional API-based counting via a provider.
"""
from __future__ import annotations

from typing import Any


class TokenEstimator:
    """Estimate or count tokens for messages and text.

    Args:
        provider: Optional provider instance that supports a
            ``count_tokens`` method.  When absent, :meth:`count_messages`
            returns ``None``.
    """

    def __init__(self, provider: Any | None = None) -> None:
        self._provider = provider
        self._cache: dict[int, int] = {}

    async def count_messages(
        self,
        messages: list,
        tools: list | None = None,
    ) -> int | None:
        """Try API-based token counting with caching.

        Returns ``None`` if no provider is configured or if the provider
        raises an exception.
        """
        last_content = ""
        if messages:
            last = messages[-1]
            if hasattr(last, "content"):
                last_content = last.content or ""
            elif isinstance(last, dict):
                last_content = last.get("content", "") or ""
        cache_key = hash((len(messages), str(last_content), len(tools or [])))
        if cache_key in self._cache:
            return self._cache[cache_key]
        if self._provider and hasattr(self._provider, "count_tokens"):
            try:
                count = await self._provider.count_tokens(messages, tools)
                self._cache[cache_key] = count
                return count
            except Exception:
                return None
        return None

    def estimate(self, text: str) -> int:
        """Fast heuristic estimate: ``len(text) // 4``."""
        return len(text) // 4

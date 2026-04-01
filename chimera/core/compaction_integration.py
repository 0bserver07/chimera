from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.hooks.emitter import HookEmitter
    from chimera.types import Message

class CompactionIntegration:
    """Integration between the agent loop and context compaction."""

    def __init__(self, compressor=None, estimator=None, emitter: HookEmitter | None = None):
        self._compressor = compressor
        self._estimator = estimator
        self._emitter = emitter

    async def auto_compact_if_needed(
        self,
        messages: list[Message],
        token_budget: int,
        threshold: float = 0.8,
    ) -> tuple[list[Message], bool]:
        """Auto-compact if messages exceed threshold of token budget.
        Returns (possibly-compacted messages, whether compaction fired).
        """
        if self._compressor is None or self._estimator is None:
            return messages, False

        estimated = await self._estimate_tokens(messages)
        if estimated < token_budget * threshold:
            return messages, False

        try:
            if self._emitter:
                from chimera.hooks.events import HookEvent
                await self._emitter.emit(HookEvent.PRE_COMPACT)

            compacted = await self._compressor.compress(messages)

            if self._emitter:
                from chimera.hooks.events import HookEvent
                await self._emitter.emit(HookEvent.POST_COMPACT)

            return compacted, True
        except Exception:
            return messages, False

    async def reactive_compact(self, messages: list[Message]) -> list[Message] | None:
        """Emergency compaction on prompt-too-long. Returns None if fails."""
        if self._compressor is None:
            return None
        try:
            return await self._compressor.compress(messages, aggressive=True)
        except Exception:
            return None

    async def _estimate_tokens(self, messages: list[Message]) -> int:
        if self._estimator:
            count = await self._estimator.count_messages(messages)
            if count is not None:
                return count
        # Fallback: rough estimate
        total_chars = sum(len(getattr(m, 'content', '')) for m in messages)
        return total_chars // 4

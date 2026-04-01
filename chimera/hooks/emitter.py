"""Centralized hook emission helper.

Used by AgentLoop, AgentSpawner, CompactionIntegration, etc. to fire
hooks without duplicating executor/matcher plumbing.
"""
from __future__ import annotations

from chimera.hooks.events import HookEvent
from chimera.hooks.executor import HookExecutor
from chimera.hooks.types import HookInput, HookMatcher, HookOutput

__all__ = ["HookEmitter"]


class HookEmitter:
    """Centralized hook emission.

    Used by AgentLoop, AgentSpawner, CompactionIntegration, etc.
    """

    def __init__(
        self,
        executor: HookExecutor | None = None,
        matchers: list[HookMatcher] | None = None,
    ) -> None:
        self._executor = executor
        self._matchers = matchers or []

    async def emit(self, event: HookEvent, **kwargs) -> HookOutput:
        """Fire hooks for *event* and return the merged output.

        Keyword arguments are forwarded to :class:`HookInput`.  The
        ``session_id`` kwarg is extracted and passed separately; all
        remaining kwargs are set as attributes on the input object.
        """
        if not self._executor:
            return HookOutput()

        session_id = kwargs.pop("session_id", "")
        input_data = HookInput(event=event, session_id=session_id, **kwargs)
        return await self._executor.execute(event, input_data, self._matchers)

    @property
    def active(self) -> bool:
        """Return ``True`` if an executor is configured."""
        return self._executor is not None

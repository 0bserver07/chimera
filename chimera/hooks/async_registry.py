"""AsyncHookRegistry — track and poll async hook tasks."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from chimera.hooks.events import HookEvent
from chimera.hooks.types import HookOutput


@dataclass
class PendingAsyncHook:
    """Tracks a single in-flight async hook execution."""

    hook_id: str
    hook_name: str
    event: HookEvent
    start_time: float
    timeout_ms: int = 15000
    task: asyncio.Task[HookOutput] | None = None
    completed: bool = False
    result: HookOutput | None = None


class AsyncHookRegistry:
    """Registry of pending async hook tasks.

    Used to fire hooks asynchronously and poll for their completion
    later in the agent loop.
    """

    def __init__(self) -> None:
        self._pending: list[PendingAsyncHook] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        hook_id: str,
        hook_name: str,
        event: HookEvent,
        task: asyncio.Task[HookOutput],
        timeout_ms: int = 15000,
    ) -> None:
        """Register an async hook task for tracking."""
        pending = PendingAsyncHook(
            hook_id=hook_id,
            hook_name=hook_name,
            event=event,
            start_time=time.time(),
            timeout_ms=timeout_ms,
            task=task,
        )
        self._pending.append(pending)

    async def check_completed(self) -> list[PendingAsyncHook]:
        """Poll pending tasks and return those that completed or timed out.

        Timed-out tasks are cancelled. Completed entries are removed from
        the internal pending list.
        """
        now = time.time()
        completed: list[PendingAsyncHook] = []
        still_pending: list[PendingAsyncHook] = []

        for ph in self._pending:
            if ph.task is None:
                ph.completed = True
                ph.result = HookOutput()
                completed.append(ph)
                continue

            elapsed_ms = (now - ph.start_time) * 1000

            if ph.task.done():
                ph.completed = True
                try:
                    ph.result = ph.task.result()
                except Exception:
                    ph.result = HookOutput()
                completed.append(ph)
            elif elapsed_ms >= ph.timeout_ms:
                ph.task.cancel()
                try:
                    await ph.task
                except (asyncio.CancelledError, Exception):
                    pass
                ph.completed = True
                ph.result = HookOutput()
                completed.append(ph)
            else:
                still_pending.append(ph)

        self._pending = still_pending
        return completed

    async def finalize_all(self) -> None:
        """Cancel all pending tasks and clear the registry."""
        for ph in self._pending:
            if ph.task is not None and not ph.task.done():
                ph.task.cancel()
                try:
                    await ph.task
                except (asyncio.CancelledError, Exception):
                    pass
        self._pending.clear()

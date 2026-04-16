"""SessionHookManager — runtime hook registration for a single session."""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, Callable

from chimera.hooks.events import HookEvent
from chimera.hooks.hook_types import CommandHook, FunctionHook, HookMatcher


class SessionHookManager:
    """Manages hooks added at runtime during a session.

    Hooks are stored per-event and identified by a unique hook_id so they
    can be removed later.
    """

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[tuple[str, HookMatcher]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Add hooks
    # ------------------------------------------------------------------

    def add_command_hook(
        self,
        event: HookEvent,
        command: str,
        matcher: str | None = None,
        timeout: int = 60,
    ) -> str:
        """Register a command hook. Returns a hook_id."""
        hook_id = str(uuid.uuid4())
        hook = CommandHook(command=command, timeout=timeout)
        hm = HookMatcher(hooks=[hook], matcher=matcher, source="session")
        self._hooks[event].append((hook_id, hm))
        return hook_id

    def add_function_hook(
        self,
        event: HookEvent,
        callback: Callable[..., Any],
        matcher: str | None = None,
        timeout: int = 5,
        error_message: str = "Check failed",
    ) -> str:
        """Register a function hook. Returns a hook_id."""
        hook_id = str(uuid.uuid4())
        hook = FunctionHook(
            callback=callback,
            id=hook_id,
            timeout=timeout,
            error_message=error_message,
        )
        hm = HookMatcher(hooks=[hook], matcher=matcher, source="session")
        self._hooks[event].append((hook_id, hm))
        return hook_id

    # ------------------------------------------------------------------
    # Remove
    # ------------------------------------------------------------------

    def remove_hook(self, hook_id: str) -> bool:
        """Remove a hook by its id. Returns True if found and removed."""
        for event, entries in self._hooks.items():
            for i, (hid, _) in enumerate(entries):
                if hid == hook_id:
                    entries.pop(i)
                    return True
        return False

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_matchers(self, event: HookEvent) -> list[HookMatcher]:
        """Return all HookMatchers registered for *event*."""
        return [hm for _, hm in self._hooks.get(event, [])]

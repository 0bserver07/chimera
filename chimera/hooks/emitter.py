"""Centralized hook emission helper.

Used by AgentLoop, AgentSpawner, CompactionIntegration, etc. to fire
hooks without duplicating executor/matcher plumbing.

Wired events (fired from call sites):
- AgentLoop: SESSION_START, SESSION_END, STOP, STOP_FAILURE,
  PRE_TOOL_USE, POST_TOOL_USE, POST_TOOL_USE_FAILURE,
  NOTIFICATION, PERMISSION_DENIED
- AgentSpawner: SUBAGENT_START, SUBAGENT_STOP, TEAMMATE_IDLE
- CompactionIntegration: PRE_COMPACT, POST_COMPACT
- TaskManager: TASK_CREATED, TASK_COMPLETED
- SlashCommandProcessor: USER_PROMPT_SUBMIT
- FileWatcher: FILE_CHANGED, CWD_CHANGED
- PermissionChecker: PERMISSION_REQUEST, PERMISSION_DENIED
- PermissionPromptHandler: ELICITATION, ELICITATION_RESULT
- chimera.cli.main: SETUP
- chimera.mink.settings: CONFIG_CHANGE
- chimera.tools.worktree_tool: WORKTREE_CREATE, WORKTREE_REMOVE
- chimera.context.agent_memory + chimera.otter.rules: INSTRUCTIONS_LOADED
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from chimera.hooks.events import HookEvent
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import HookInput, HookMatcher, HookOutput

__all__ = ["HookEmitter", "get_global_emitter", "set_global_emitter"]


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

    async def emit(self, event: HookEvent, **kwargs: Any) -> HookOutput:
        """Fire hooks for *event* and return the merged output.

        Keyword arguments are forwarded to :class:`HookInput`.  The
        ``session_id`` kwarg is extracted and passed separately; all
        remaining kwargs are set as attributes on the input object.
        Unknown kwargs are dropped silently so call sites can attach
        arbitrary context without breaking older HookInput shapes.
        """
        if not self._executor:
            return HookOutput()

        session_id = kwargs.pop("session_id", "")
        input_kwargs = _filter_hook_input_kwargs(kwargs)
        input_data = HookInput(event=event, session_id=session_id, **input_kwargs)
        return await self._executor.execute(event, input_data, self._matchers)

    def emit_sync(self, event: HookEvent, **kwargs: Any) -> HookOutput:
        """Synchronous wrapper for :meth:`emit`.

        Safe to call from non-async code paths. If called from inside a
        running event loop, the work is dispatched to a worker thread so
        we never deadlock on ``asyncio.run`` re-entry. If no executor is
        configured, returns a default :class:`HookOutput` cheaply without
        spinning up an event loop.
        """
        if not self._executor:
            return HookOutput()

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop -> safe to drive a fresh one synchronously.
            return asyncio.run(self.emit(event, **kwargs))

        # We're inside a running loop; offload to a worker thread so the
        # caller (which is sync) doesn't deadlock awaiting itself.
        result_holder: dict[str, HookOutput] = {}

        def _runner() -> None:
            result_holder["out"] = asyncio.run(self.emit(event, **kwargs))

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()
        return result_holder.get("out", HookOutput())

    @property
    def active(self) -> bool:
        """Return ``True`` if an executor is configured."""
        return self._executor is not None


# ---------------------------------------------------------------------------
# Module-level "global" emitter
# ---------------------------------------------------------------------------
#
# Some emit sites (config loaders, rules ingest, the worktree tool) live
# outside the AgentLoop call graph and have no natural way to receive a
# constructor-injected emitter. For those, callers may register a process-
# wide emitter via :func:`set_global_emitter` and read it back via
# :func:`get_global_emitter`. Both helpers degrade gracefully when no
# emitter has been registered: callers always see a no-op
# :class:`HookEmitter` so backwards-compat behavior (no hooks fire) is
# preserved.

_global_emitter: HookEmitter | None = None
_global_lock = threading.Lock()


def set_global_emitter(emitter: HookEmitter | None) -> None:
    """Register the process-wide hook emitter.

    Pass ``None`` to clear it.  Thread-safe; takes a short lock.
    """
    global _global_emitter
    with _global_lock:
        _global_emitter = emitter


def get_global_emitter() -> HookEmitter:
    """Return the registered global emitter, or a no-op fallback.

    The fallback emitter has no executor, so its :meth:`HookEmitter.emit`
    and :meth:`HookEmitter.emit_sync` calls return a default
    :class:`HookOutput` without doing any work.
    """
    with _global_lock:
        if _global_emitter is not None:
            return _global_emitter
    return HookEmitter()


_VALID_HOOK_INPUT_FIELDS: frozenset[str] = frozenset(
    {
        "tool_name",
        "tool_input",
        "tool_output",
        "tool_error",
        "user_prompt",
        "messages",
    }
)


def _filter_hook_input_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop kwargs that aren't part of the :class:`HookInput` constructor.

    Unknown keys are silently discarded so call sites can pass arbitrary
    context (e.g. ``decision``, ``branch``, ``path``) without raising
    ``TypeError`` from older :class:`HookInput` definitions.
    """
    return {k: v for k, v in kwargs.items() if k in _VALID_HOOK_INPUT_FIELDS}

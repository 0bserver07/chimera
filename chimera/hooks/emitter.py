"""Centralized hook emission helper.

Used by AgentLoop, AgentSpawner, CompactionIntegration, etc. to fire
hooks without duplicating executor/matcher plumbing.

Wired events (fired from call sites):
- AgentLoop: SESSION_START, SESSION_END, STOP, STOP_FAILURE,
  PRE_TOOL_USE, POST_TOOL_USE, POST_TOOL_USE_FAILURE,
  NOTIFICATION, PERMISSION_DENIED
- AgentSpawner: SUBAGENT_START, SUBAGENT_STOP
- CompactionIntegration: PRE_COMPACT, POST_COMPACT
- TaskManager: TASK_CREATED, TASK_COMPLETED
- SlashCommandProcessor: USER_PROMPT_SUBMIT
- FileWatcher: FILE_CHANGED, CWD_CHANGED

Pending (require integration points not yet built):
- SETUP: Fired during initial environment setup
- ELICITATION/ELICITATION_RESULT: MCP URL elicitation flow
- CONFIG_CHANGE: Settings file modification detection
- WORKTREE_CREATE/WORKTREE_REMOVE: Git worktree operations
- INSTRUCTIONS_LOADED: CHIMERA.md/CLAUDE.md loading
- TEAMMATE_IDLE: Multi-agent team coordination
"""
from __future__ import annotations

from chimera.hooks.events import HookEvent
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import HookInput, HookMatcher, HookOutput

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

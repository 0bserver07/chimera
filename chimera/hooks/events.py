"""HookEvent enum — all lifecycle events the hook system can fire."""
from __future__ import annotations

from enum import Enum


class HookEvent(Enum):
    """Enumeration of all hook events in chimera."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    SETUP = "Setup"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    NOTIFICATION = "Notification"
    TEAMMATE_IDLE = "TeammateIdle"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"
    CONFIG_CHANGE = "ConfigChange"
    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"

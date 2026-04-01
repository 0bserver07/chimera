"""Tests for chimera.hooks.events — HookEvent enum."""
from __future__ import annotations

from chimera.hooks.events import HookEvent

EXPECTED_EVENTS = [
    "PRE_TOOL_USE",
    "POST_TOOL_USE",
    "POST_TOOL_USE_FAILURE",
    "PERMISSION_REQUEST",
    "PERMISSION_DENIED",
    "USER_PROMPT_SUBMIT",
    "SESSION_START",
    "SESSION_END",
    "SETUP",
    "SUBAGENT_START",
    "SUBAGENT_STOP",
    "STOP",
    "STOP_FAILURE",
    "PRE_COMPACT",
    "POST_COMPACT",
    "NOTIFICATION",
    "TEAMMATE_IDLE",
    "TASK_CREATED",
    "TASK_COMPLETED",
    "ELICITATION",
    "ELICITATION_RESULT",
    "CONFIG_CHANGE",
    "WORKTREE_CREATE",
    "WORKTREE_REMOVE",
    "INSTRUCTIONS_LOADED",
    "CWD_CHANGED",
    "FILE_CHANGED",
]


def test_all_27_events_exist():
    """HookEvent enum contains exactly 27 members."""
    assert len(HookEvent) == 27


def test_each_event_name():
    """Every expected event name exists as a HookEvent member."""
    for name in EXPECTED_EVENTS:
        assert hasattr(HookEvent, name), f"Missing HookEvent.{name}"


def test_event_values_are_strings():
    """All HookEvent values are strings matching their names."""
    for event in HookEvent:
        assert isinstance(event.value, str)

"""Tests for chimera.hooks.session_hooks — SessionHookManager."""
from __future__ import annotations

from chimera.hooks.events import HookEvent
from chimera.hooks.session_hooks import SessionHookManager
from chimera.hooks.types import HookOutput


# ---------------------------------------------------------------------------
# add + get
# ---------------------------------------------------------------------------


def test_add_command_hook():
    mgr = SessionHookManager()
    hook_id = mgr.add_command_hook(HookEvent.PRE_TOOL_USE, "echo check", matcher="bash")
    assert isinstance(hook_id, str)
    matchers = mgr.get_matchers(HookEvent.PRE_TOOL_USE)
    assert len(matchers) == 1
    assert matchers[0].matcher == "bash"
    assert matchers[0].hooks[0].command == "echo check"


def test_add_function_hook():
    def my_fn(inp):
        return HookOutput()

    mgr = SessionHookManager()
    hook_id = mgr.add_function_hook(HookEvent.POST_TOOL_USE, my_fn, matcher="Write")
    assert isinstance(hook_id, str)
    matchers = mgr.get_matchers(HookEvent.POST_TOOL_USE)
    assert len(matchers) == 1
    assert matchers[0].matcher == "Write"
    assert matchers[0].hooks[0].callback is my_fn


def test_add_command_hook_default_timeout():
    mgr = SessionHookManager()
    mgr.add_command_hook(HookEvent.STOP, "verify.py")
    matchers = mgr.get_matchers(HookEvent.STOP)
    assert matchers[0].hooks[0].timeout == 60


def test_add_function_hook_custom_timeout():
    mgr = SessionHookManager()
    mgr.add_function_hook(
        HookEvent.STOP,
        lambda x: HookOutput(),
        timeout=15,
        error_message="bad",
    )
    matchers = mgr.get_matchers(HookEvent.STOP)
    assert matchers[0].hooks[0].timeout == 15
    assert matchers[0].hooks[0].error_message == "bad"


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_hook():
    mgr = SessionHookManager()
    hid = mgr.add_command_hook(HookEvent.PRE_TOOL_USE, "echo x")
    assert mgr.remove_hook(hid) is True
    assert mgr.get_matchers(HookEvent.PRE_TOOL_USE) == []


def test_remove_nonexistent_hook():
    mgr = SessionHookManager()
    assert mgr.remove_hook("nonexistent-id") is False


# ---------------------------------------------------------------------------
# get_matchers returns empty for unregistered events
# ---------------------------------------------------------------------------


def test_get_matchers_empty():
    mgr = SessionHookManager()
    assert mgr.get_matchers(HookEvent.SESSION_START) == []


# ---------------------------------------------------------------------------
# multiple hooks on same event
# ---------------------------------------------------------------------------


def test_multiple_hooks_same_event():
    mgr = SessionHookManager()
    mgr.add_command_hook(HookEvent.PRE_TOOL_USE, "check1", matcher="bash")
    mgr.add_command_hook(HookEvent.PRE_TOOL_USE, "check2", matcher="Write")
    matchers = mgr.get_matchers(HookEvent.PRE_TOOL_USE)
    assert len(matchers) == 2

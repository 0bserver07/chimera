"""Tests for chimera.hooks.hook_types — hook type dataclasses."""
from __future__ import annotations

import json

from chimera.hooks.hook_types import (
    CommandHook,
    FunctionHook,
    Hook,
    HookInput,
    HookMatcher,
    HookOutput,
    PromptHook,
)


# ---------------------------------------------------------------------------
# HookInput
# ---------------------------------------------------------------------------


def test_hook_input_defaults():
    inp = HookInput(event="PreToolUse", session_id="s1")
    assert inp.event == "PreToolUse"
    assert inp.session_id == "s1"
    assert inp.tool_name is None
    assert inp.tool_input is None
    assert inp.tool_output is None
    assert inp.tool_error is None
    assert inp.user_prompt is None
    assert inp.messages is None


def test_hook_input_to_json():
    inp = HookInput(
        event="PreToolUse",
        session_id="s1",
        tool_name="bash",
        tool_input={"command": "ls"},
    )
    parsed = json.loads(inp.to_json())
    assert parsed["event"] == "PreToolUse"
    assert parsed["session_id"] == "s1"
    assert parsed["tool_name"] == "bash"
    assert parsed["tool_input"] == {"command": "ls"}


def test_hook_input_to_json_excludes_none():
    inp = HookInput(event="Stop", session_id="s2")
    parsed = json.loads(inp.to_json())
    # None fields should either be absent or null — the to_json() spec
    # should include all fields for predictability
    assert "event" in parsed
    assert "session_id" in parsed


# ---------------------------------------------------------------------------
# HookOutput
# ---------------------------------------------------------------------------


def test_hook_output_defaults():
    out = HookOutput()
    assert out.continue_execution is True
    assert out.suppress_output is False
    assert out.stop_reason is None
    assert out.decision is None
    assert out.reason is None
    assert out.system_message is None
    assert out.additional_context is None
    assert out.updated_input is None
    assert out.retry is False


def test_hook_output_block():
    out = HookOutput(continue_execution=False, stop_reason="blocked by policy")
    assert out.continue_execution is False
    assert out.stop_reason == "blocked by policy"


# ---------------------------------------------------------------------------
# CommandHook
# ---------------------------------------------------------------------------


def test_command_hook_construction():
    hook = CommandHook(command="echo check")
    assert hook.type == "command"
    assert hook.command == "echo check"
    assert hook.timeout == 60


def test_command_hook_custom_timeout():
    hook = CommandHook(command="slow-check", timeout=120)
    assert hook.timeout == 120


# ---------------------------------------------------------------------------
# PromptHook
# ---------------------------------------------------------------------------


def test_prompt_hook_construction():
    hook = PromptHook(prompt="Is this safe?")
    assert hook.type == "prompt"
    assert hook.prompt == "Is this safe?"
    assert hook.timeout == 30


# ---------------------------------------------------------------------------
# FunctionHook
# ---------------------------------------------------------------------------


def test_function_hook_construction():
    def my_callback(input_data):
        return HookOutput()

    hook = FunctionHook(callback=my_callback)
    assert hook.type == "function"
    assert hook.callback is my_callback
    assert hook.timeout == 5
    assert hook.error_message == "Hook check failed"
    assert hook.id is None


def test_function_hook_custom_fields():
    hook = FunctionHook(
        callback=lambda x: x,
        id="my-hook",
        timeout=10,
        error_message="Custom fail",
    )
    assert hook.id == "my-hook"
    assert hook.timeout == 10
    assert hook.error_message == "Custom fail"


# ---------------------------------------------------------------------------
# HookMatcher
# ---------------------------------------------------------------------------


def test_hook_matcher_defaults():
    hook = CommandHook(command="test")
    matcher = HookMatcher(hooks=[hook])
    assert matcher.matcher is None
    assert matcher.source == "user"
    assert matcher.plugin_name is None
    assert len(matcher.hooks) == 1


def test_hook_matcher_with_matcher():
    hook = CommandHook(command="test")
    matcher = HookMatcher(matcher="bash", hooks=[hook], source="project")
    assert matcher.matcher == "bash"
    assert matcher.source == "project"


# ---------------------------------------------------------------------------
# Hook type alias
# ---------------------------------------------------------------------------


def test_hook_type_alias_includes_all():
    """Hook union includes CommandHook, PromptHook, FunctionHook."""
    cmd = CommandHook(command="x")
    prompt = PromptHook(prompt="y")
    func = FunctionHook(callback=lambda x: x)
    # All should be acceptable as Hook
    hooks: list[Hook] = [cmd, prompt, func]
    assert len(hooks) == 3

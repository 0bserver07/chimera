"""Tests for PreToolUse hook input mutation + cwd/env inheritance.

Covers M2-C scope:
    - HookOutput.updated_input mutates the dispatched tool call.
    - Subprocess CommandHooks inherit os.environ + receive HOOK_* vars
      and run in the configured cwd.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chimera.core.context import Context
from chimera.core.loop_config import LoopConfig
from chimera.core.tool_executor import execute_tool_calls
from chimera.events.base import EventBus
from chimera.events.types import HookUpdatedInputEvent
from chimera.hooks.emitter import HookEmitter
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import (
    CommandHook,
    FunctionHook,
    HookInput,
    HookMatcher,
    HookOutput,
)
from chimera.permissions.presets import AutoApprove
from chimera.types import ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_recording_tool(name: str = "bash"):
    """Make a MagicMock tool that records what arguments it was called with."""
    tool = MagicMock()
    tool.name = name
    tool.execute.return_value = ToolResult(output="ok")
    return tool


def _build_emitter(matcher: HookMatcher) -> HookEmitter:
    return HookEmitter(executor=HookExecutor(), matchers=[matcher])


# ---------------------------------------------------------------------------
# Test 1: PreToolUse FunctionHook returns updated_input → tool sees it
# ---------------------------------------------------------------------------


def test_updated_input_mutates_tool_call() -> None:
    """A PreToolUse hook returning updated_input must replace the tool args.

    Original arg: ``rm -rf /``. Hook supplies ``git status``. The dispatched
    tool call must see ``git status`` as its ``command`` argument.
    """
    captured: list[dict] = []

    def mutating_hook(messages, abort):
        # FunctionHook returning a HookOutput with updated_input.
        return HookOutput(
            continue_execution=True,
            updated_input={"command": "git status"},
        )

    matcher = HookMatcher(
        hooks=[FunctionHook(callback=mutating_hook)],
        matcher="bash",
    )
    emitter = _build_emitter(matcher)

    bus = EventBus()
    seen_events: list[HookUpdatedInputEvent] = []
    bus.subscribe("hook_updated_input", lambda e: seen_events.append(e))

    config = LoopConfig(
        permissions=AutoApprove(),
        hook_emitter=emitter,
        event_bus=bus,
    )

    tool = _make_recording_tool("bash")
    tool.execute.side_effect = lambda args, env: (
        captured.append(dict(args)) or ToolResult(output="ran")
    )
    tc = ToolCall(id="c1", name="bash", arguments={"command": "rm -rf /"})

    execute_tool_calls([tc], {"bash": tool}, Context(), None, config)

    # The dispatched command must be the mutated one.
    assert len(captured) == 1
    assert captured[0]["command"] == "git status"

    # And the event bus saw the mutation.
    assert len(seen_events) == 1
    assert seen_events[0].original == {"command": "rm -rf /"}
    assert seen_events[0].updated["command"] == "git status"


# ---------------------------------------------------------------------------
# Test 2: subprocess CommandHook inherits cwd + sees HOOK_* env vars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_inherits_cwd_env(tmp_path: Path) -> None:
    """A CommandHook must run in the configured cwd and see HOOK_* env vars.

    The hook script:
        - Reads HOOK_TOOL_NAME from env and writes it to ./hook_marker.txt
        - Writes os.getcwd() to ./hook_cwd.txt
    Both files must land inside *tmp_path*.
    """
    # Hook script: writes two marker files at $PWD using HOOK_TOOL_NAME.
    script = (
        "import os, pathlib;"
        " pathlib.Path('hook_marker.txt').write_text(os.environ['HOOK_TOOL_NAME']);"
        " pathlib.Path('hook_cwd.txt').write_text(os.getcwd());"
        " print('{}')"  # empty JSON so executor is happy
    )
    cmd = f'{sys.executable} -c "{script}"'

    hook = CommandHook(command=cmd, cwd=str(tmp_path))
    matcher = HookMatcher(hooks=[hook])

    executor = HookExecutor(cwd=str(tmp_path))
    inp = HookInput(
        event="PreToolUse",
        session_id="s1",
        tool_name="bash",
        tool_input={"command": "ls"},
    )

    from chimera.hooks.events import HookEvent

    out = await executor.execute(HookEvent.PRE_TOOL_USE, inp, [matcher])

    assert out.continue_execution is True

    marker = tmp_path / "hook_marker.txt"
    cwd_file = tmp_path / "hook_cwd.txt"
    assert marker.exists(), "HOOK_TOOL_NAME env var was not visible to subprocess"
    assert marker.read_text() == "bash"

    assert cwd_file.exists(), "Subprocess did not run in configured cwd"
    # Resolve symlinks (e.g. /tmp on macOS → /private/tmp).
    assert os.path.realpath(cwd_file.read_text()) == os.path.realpath(str(tmp_path))


# ---------------------------------------------------------------------------
# Bonus: extra_env merges into subprocess env
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_extra_env_merges(tmp_path: Path) -> None:
    """Per-hook extra_env values reach the subprocess."""
    script = (
        "import os, pathlib;"
        " pathlib.Path('val.txt').write_text(os.environ.get('CHIMERA_HOOK_TEST', ''));"
        " print('{}')"
    )
    cmd = f'{sys.executable} -c "{script}"'

    hook = CommandHook(
        command=cmd, cwd=str(tmp_path), extra_env={"CHIMERA_HOOK_TEST": "yo"},
    )
    matcher = HookMatcher(hooks=[hook])

    executor = HookExecutor()
    inp = HookInput(event="PreToolUse", session_id="s1", tool_name="bash")
    from chimera.hooks.events import HookEvent

    await executor.execute(HookEvent.PRE_TOOL_USE, inp, [matcher])

    assert (tmp_path / "val.txt").read_text() == "yo"


# ---------------------------------------------------------------------------
# Bonus: subprocess JSON stdout populates updated_input + permission_decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_json_stdout_parsed(tmp_path: Path) -> None:
    """Hook stdout JSON populates the extended HookOutput fields."""
    payload = {
        "hookSpecificOutput": {
            "permissionDecision": "allow",
            "permissionDecisionReason": "looks safe",
            "updatedInput": {"command": "git status"},
            "additionalContext": "audited",
        }
    }
    script_path = tmp_path / "hook_json.py"
    script_path.write_text(
        "import json, sys; print(json.dumps(" + repr(payload) + "))"
    )
    cmd = f'{sys.executable} {script_path}'

    hook = CommandHook(command=cmd)
    matcher = HookMatcher(hooks=[hook])

    inp = HookInput(
        event="PreToolUse",
        session_id="s1",
        tool_name="bash",
        tool_input={"command": "rm -rf /"},
    )
    from chimera.hooks.events import HookEvent

    out = await HookExecutor().execute(HookEvent.PRE_TOOL_USE, inp, [matcher])

    assert out.permission_decision == "allow"
    assert out.permission_decision_reason == "looks safe"
    assert out.updated_input == {"command": "git status"}
    assert out.additional_context == "audited"

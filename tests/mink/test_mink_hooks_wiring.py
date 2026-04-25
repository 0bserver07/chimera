"""Regression tests for AUDIT.md B-4 second half — settings.json hooks wired.

`MinkSettings.hooks` was parsed-but-unused before this fix. The CLI now
translates each event-name -> list-of-spec entry into a
:class:`~chimera.hooks.executor.HookExecutor` wrapped in a
:class:`~chimera.hooks.emitter.HookEmitter`, then passes it to
:class:`~chimera.core.loop_config.LoopConfig.hook_emitter` so PreToolUse
and friends fire end-to-end during a one-shot ``-p`` run.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


# --- Hook 1: settings.json -> HookEmitter wiring -----------------------------


def test_hooks_load_from_settings(tmp_path: Path) -> None:
    """A PreToolUse command-hook entry in settings.json builds a HookEmitter."""
    from chimera.mink.cli import _build_hook_emitter
    from chimera.mink.settings import load_mink_settings

    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "echo HOOK_FIRED"},
                            ],
                        },
                    ],
                },
            },
        ),
    )

    settings = load_mink_settings(cwd=project)
    assert "PreToolUse" in settings.hooks
    emitter = _build_hook_emitter(dict(settings.hooks))
    assert emitter is not None, "expected a HookEmitter when hooks are declared"
    assert emitter.active, "emitter must be active when an executor is wired"


# --- Hook 2: hook actually fires before a tool call --------------------------


def test_hook_fires_before_tool(tmp_path: Path) -> None:
    """``HookEmitter.emit`` runs the registered command and merges its output.

    Mirrors the synchronous PreToolUse path in
    :mod:`chimera.core.tool_executor`: emit is invoked with a tool_name and
    tool_input, the command runs, and the merged ``HookOutput`` reflects
    that the call should continue (no deny).
    """
    from chimera.hooks.events import HookEvent
    from chimera.mink.cli import _build_hook_emitter

    marker = tmp_path / "fired.txt"
    settings_hooks = {
        "PreToolUse": [
            {
                # Use the flat form to verify both shapes work.
                "type": "command",
                # WHY: write a marker file so the test can assert the
                # subprocess actually executed (exit-code-only checks
                # leave a window where the hook never ran).
                "command": f"echo fired > {marker}",
            },
        ],
    }
    emitter = _build_hook_emitter(settings_hooks)
    assert emitter is not None

    output = asyncio.run(
        emitter.emit(
            HookEvent.PRE_TOOL_USE,
            session_id="t",
            tool_name="bash",
            tool_input={"command": "echo hi"},
        ),
    )
    assert output.continue_execution is True
    assert marker.exists(), "PreToolUse hook command did not execute"
    assert "fired" in marker.read_text()


# --- Hook 3: empty hooks block is a no-op -----------------------------------


def test_no_hooks_no_error() -> None:
    """``_build_hook_emitter({})`` returns ``None`` so callers stay opt-in.

    The default mink invocation (no settings.json or empty hooks key)
    must produce zero hook overhead and no exception. Returning ``None``
    keeps :class:`LoopConfig.hook_emitter` ``None`` which is the same
    state as before the fix — guaranteed backward compatibility.
    """
    from chimera.mink.cli import _build_hook_emitter

    assert _build_hook_emitter({}) is None
    # Also verify a malformed-but-tolerable shape (event with no list value)
    # silently degrades to None instead of raising.
    assert _build_hook_emitter({"PreToolUse": "not-a-list"}) is None  # type: ignore[arg-type]


# --- Hook 4: bonus regression — flat vs nested spec shapes both work -------


@pytest.mark.parametrize(
    "spec",
    [
        # Flat shape
        {"type": "command", "command": "true"},
        # Nested shape (CC's canonical form)
        {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "true"}],
        },
    ],
)
def test_hook_spec_shapes_both_supported(spec: dict) -> None:
    """Both the flat and the nested CC hook shapes parse cleanly."""
    from chimera.mink.cli import _build_hook_emitter

    emitter = _build_hook_emitter({"PreToolUse": [spec]})
    assert emitter is not None
    assert emitter.active

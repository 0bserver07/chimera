"""Audit B-4 (second half): ``.claude/settings.json`` hooks reach LoopConfig.

The first half of B-4 (permissions allow/ask/deny rules) was closed by
AGENT-FIX-E. This file pins the hook half: the ``hooks`` block parsed by
``MinkSettings`` is now translated into a :class:`HookEmitter` that the
loop fires on PreToolUse / PostToolUse, so a hook returning
``{"continue": false}`` actually blocks the dispatched tool.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any



# ---------------------------------------------------------------------------
# _build_hook_emitter unit-level
# ---------------------------------------------------------------------------


def test_build_hook_emitter_returns_none_when_no_hooks():
    """No hooks block → no emitter (preserves old behavior exactly)."""
    from chimera.mink.cli import _build_hook_emitter

    assert _build_hook_emitter({}) is None
    assert _build_hook_emitter({"PreToolUse": []}) is None


def test_build_hook_emitter_parses_flat_command_shape():
    """The flat CC shape ``{type,command,matcher}`` translates to a CommandHook."""
    from chimera.hooks.emitter import HookEmitter
    from chimera.hooks.hook_types import CommandHook
    from chimera.mink.cli import _build_hook_emitter

    emitter = _build_hook_emitter(
        {
            "PreToolUse": [
                {"type": "command", "command": "echo guard", "matcher": "Bash"},
            ],
        }
    )
    assert isinstance(emitter, HookEmitter)
    matchers = emitter._matchers  # internal but stable for tests
    assert len(matchers) == 1
    assert matchers[0].matcher == "Bash"
    assert isinstance(matchers[0].hooks[0], CommandHook)
    assert matchers[0].hooks[0].command == "echo guard"


def test_build_hook_emitter_parses_nested_hooks_shape():
    """The CC nested shape ``{matcher, hooks: [{type,command}]}`` is also accepted."""
    from chimera.hooks.hook_types import CommandHook
    from chimera.mink.cli import _build_hook_emitter

    emitter = _build_hook_emitter(
        {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "echo nested"},
                    ],
                }
            ],
        }
    )
    assert emitter is not None
    matchers = emitter._matchers
    assert matchers[0].matcher == "Bash"
    assert isinstance(matchers[0].hooks[0], CommandHook)
    assert matchers[0].hooks[0].command == "echo nested"


def test_build_hook_emitter_skips_unparseable_entries():
    """Garbage entries are silently dropped — never raise on bad JSON shapes."""
    from chimera.mink.cli import _build_hook_emitter

    out = _build_hook_emitter(
        {
            "PreToolUse": [
                {"type": "command"},  # missing command field
                "not-a-dict",
                {"type": "function", "callback": None},  # functions can't come from JSON
            ],
        }
    )
    assert out is None  # nothing usable parsed


# ---------------------------------------------------------------------------
# settings.json end-to-end loading
# ---------------------------------------------------------------------------


def test_pre_tool_use_hook_from_settings_loaded(tmp_path, monkeypatch):
    """A ``.claude/settings.json`` PreToolUse hook reaches the LoopConfig.

    Verified structurally: ``load_mink_settings`` parses the file,
    ``_build_hook_emitter`` produces a non-None emitter, and the resulting
    HookExecutor invocation against a "Bash" tool returns
    ``continue_execution=False`` because the hook command exits 2.
    """
    from chimera.hooks.events import HookEvent
    from chimera.mink.cli import _build_hook_emitter
    from chimera.mink.settings import load_mink_settings

    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    # exit 2 = block. CC contract.
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "type": "command",
                            "command": "exit 2",
                            "matcher": "Bash",
                        }
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    settings = load_mink_settings(cwd=tmp_path)
    emitter = _build_hook_emitter(dict(settings.hooks or {}))
    assert emitter is not None, (
        f"settings hooks not wired: {settings.hooks!r}"
    )

    # Drive the emitter via its public async API and assert the block.
    async def _drive():
        return await emitter.emit(
            HookEvent.PRE_TOOL_USE,
            tool_name="Bash",
            tool_input={"command": "ls"},
        )

    out = asyncio.run(_drive())
    assert out.continue_execution is False, (
        f"hook did not block: {out!r}"
    )


def test_settings_hook_fires_only_on_matching_tool(tmp_path, monkeypatch):
    """``matcher: Bash`` hook does NOT fire for a Read call."""
    from chimera.hooks.events import HookEvent
    from chimera.mink.cli import _build_hook_emitter
    from chimera.mink.settings import load_mink_settings

    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "type": "command",
                            "command": "exit 2",
                            "matcher": "Bash",
                        }
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    settings = load_mink_settings(cwd=tmp_path)
    emitter = _build_hook_emitter(dict(settings.hooks or {}))
    assert emitter is not None

    async def _drive():
        return await emitter.emit(
            HookEvent.PRE_TOOL_USE,
            tool_name="Read",  # does not match "Bash"
            tool_input={"path": "/tmp/x"},
        )

    out = asyncio.run(_drive())
    assert out.continue_execution is True, (
        f"hook fired despite matcher mismatch: {out!r}"
    )


def test_settings_and_chimera_hooks_merge(tmp_path, monkeypatch):
    """`.claude/settings.json` + `.chimera/settings.json` hooks both fire.

    The mink path loads the merged ``MinkSettings.hooks`` (via deep_merge
    in ``load_mink_settings``), so both layers' hooks should be present
    in the resulting HookEmitter.
    """
    from chimera.mink.cli import _build_hook_emitter
    from chimera.mink.settings import load_mink_settings

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "type": "command",
                            "command": "echo claude-hook",
                            "matcher": "Bash",
                        }
                    ]
                }
            }
        )
    )
    chimera_dir = tmp_path / ".chimera"
    chimera_dir.mkdir()
    (chimera_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "type": "command",
                            "command": "echo chimera-hook",
                            "matcher": "Bash",
                        }
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
    settings = load_mink_settings(cwd=tmp_path)
    emitter = _build_hook_emitter(dict(settings.hooks or {}))
    assert emitter is not None
    matchers = emitter._matchers
    cmds = [h.command for m in matchers for h in m.hooks]
    assert any("claude-hook" in c for c in cmds), cmds
    assert any("chimera-hook" in c for c in cmds), cmds


# ---------------------------------------------------------------------------
# Wiring assertion: _run_print_mode threads hook_emitter into LoopConfig
# ---------------------------------------------------------------------------


def test_run_print_mode_threads_hook_emitter_into_loop_config(tmp_path, monkeypatch):
    """``_run_print_mode`` builds and attaches the HookEmitter to LoopConfig."""
    from chimera.mink import cli as mink_cli

    # Build a deny-everything settings.json hook so we can detect it.
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "type": "command",
                            "command": "exit 2",
                            "matcher": "*",
                        }
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))

    captured: dict[str, Any] = {}

    class _StubProvider:
        model_name = "stub-model"

    class _StubAgent:
        def __init__(self, provider, tools, loop, prompt):
            captured["loop"] = loop
            self.provider = provider
            self.tools = tools
            self.loop = loop
            self.prompt = prompt

        async def async_run(self, prompt, env=None):
            class _R:
                output = "ok"
                steps = 1
                cost = 0.0
                success = True
            return _R()

    monkeypatch.setattr(mink_cli, "_build_provider", lambda model: _StubProvider())
    monkeypatch.setattr("chimera.core.agent.Agent", _StubAgent)

    args = argparse.Namespace(
        model="stub", permission_mode="default", allowed_tools="",
        resume=None, agent=None, cwd=str(tmp_path), print_mode="hi",
        output_format="json", max_steps=2, no_save=True, run_id=None,
        no_rich=False, no_color=False,
    )
    mink_cli._run_print_mode(args)

    loop = captured.get("loop")
    assert loop is not None
    assert loop.config.hook_emitter is not None, (
        "settings.json hooks were not threaded into LoopConfig.hook_emitter"
    )

"""Smoke tests for chimera.cli.permission_prompt."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from chimera.cli.permission_prompt import (
    InteractivePermissionPrompt,
    PermissionCancelled,
    PermissionRequest,
)
from chimera.permissions.rules import PermissionBehavior


def _make_prompt(keys: str, *, settings_path: Path | None = None) -> tuple[InteractivePermissionPrompt, io.StringIO]:
    """Build a prompt that replays ``keys`` one char at a time."""
    out = io.StringIO()
    pending = list(keys)

    def fake_reader(_stream):
        return pending.pop(0) if pending else ""

    prompt = InteractivePermissionPrompt(
        input_stream=io.StringIO(),
        output_stream=out,
        settings_path=settings_path,
        keystroke_reader=fake_reader,
    )
    return prompt, out


def test_approve_once_returns_allow_decision():
    prompt, out = _make_prompt("a")
    decision = prompt.prompt(
        PermissionRequest(
            tool_name="Bash",
            input_args={"command": "rm -rf node_modules"},
            reason="matches Bash(rm:*) ask-rule",
        )
    )
    assert decision.behavior is PermissionBehavior.ALLOW
    assert "approved" in out.getvalue()
    assert "Permission required" in out.getvalue()


def test_deny_once_returns_deny_decision():
    prompt, _ = _make_prompt("d")
    decision = prompt.prompt(PermissionRequest(tool_name="Bash", input_args={"command": "ls"}))
    assert decision.behavior is PermissionBehavior.DENY


def test_cancel_raises():
    prompt, _ = _make_prompt("c")
    with pytest.raises(PermissionCancelled):
        prompt.prompt(PermissionRequest(tool_name="Bash", input_args={"command": "ls"}))


def test_always_allow_persists_rule(tmp_path: Path):
    settings = tmp_path / "settings.local.json"
    prompt, _ = _make_prompt("A", settings_path=settings)
    decision = prompt.prompt(
        PermissionRequest(tool_name="Bash", input_args={"command": "git status"})
    )
    assert decision.behavior is PermissionBehavior.ALLOW
    assert settings.exists()
    payload = json.loads(settings.read_text())
    assert "Bash(command:git*)" in payload["permissions"]["allow"]


def test_help_then_approve():
    prompt, out = _make_prompt("?a")
    decision = prompt.prompt(PermissionRequest(tool_name="Bash", input_args={}))
    assert decision.behavior is PermissionBehavior.ALLOW
    # Help footer rendered twice (once in panel, once via "?")
    assert out.getvalue().count("Approve once") >= 2

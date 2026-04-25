"""Tests for the Claude-Code-compatible settings loader."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest


# WHY: chimera.mink.cli imports rich (mink extra). Skip when not installed.
pytest.importorskip("rich")
from chimera.mink.settings import (
    MinkSettings,
    MinkSettingsError,
    Permissions,
    load_mink_settings,
)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``$HOME`` and a clean cwd at ``tmp_path`` and strip env overrides."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Strip env-var overrides so each test opts in explicitly.
    for k in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OUTPUT_FORMAT",
    ):
        monkeypatch.delenv(k, raising=False)
    return project


def test_returns_defaults_when_no_files(isolated_home: Path) -> None:
    s = load_mink_settings(cwd=isolated_home)
    assert isinstance(s, MinkSettings)
    assert isinstance(s.permissions, Permissions)
    assert s.permissions.default_mode == "default"
    assert s.permissions.allow == []
    assert s.model is None


def test_user_layer_loaded(isolated_home: Path) -> None:
    home = Path(os.environ["HOME"])
    _write_json(
        home / ".claude" / "settings.json",
        {"model": "sonnet", "permissions": {"allow": ["Read"]}},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.model == "sonnet"
    assert s.permissions.allow == ["Read"]


def test_project_overrides_user_for_scalars(isolated_home: Path) -> None:
    home = Path(os.environ["HOME"])
    _write_json(home / ".claude" / "settings.json", {"model": "sonnet"})
    _write_json(isolated_home / ".claude" / "settings.json", {"model": "opus"})
    s = load_mink_settings(cwd=isolated_home)
    assert s.model == "opus"


def test_chimera_local_is_highest_file_layer(isolated_home: Path) -> None:
    _write_json(isolated_home / ".claude" / "settings.json", {"model": "sonnet"})
    _write_json(
        isolated_home / ".claude" / "settings.local.json", {"model": "haiku"}
    )
    _write_json(isolated_home / ".chimera" / "settings.json", {"model": "opus"})
    s = load_mink_settings(cwd=isolated_home)
    assert s.model == "opus"


def test_permissions_arrays_deep_additive(isolated_home: Path) -> None:
    home = Path(os.environ["HOME"])
    _write_json(
        home / ".claude" / "settings.json",
        {"permissions": {"allow": ["Read"], "deny": ["WebFetch"]}},
    )
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"permissions": {"allow": ["Bash(git status)"], "deny": ["Bash(rm *)"]}},
    )
    _write_json(
        isolated_home / ".claude" / "settings.local.json",
        {"permissions": {"allow": ["Read"], "ask": ["Bash(git push *)"]}},
    )
    s = load_mink_settings(cwd=isolated_home)
    # Concatenation, de-duplicated, order preserved.
    assert s.permissions.allow == ["Read", "Bash(git status)"]
    assert s.permissions.deny == ["WebFetch", "Bash(rm *)"]
    assert s.permissions.ask == ["Bash(git push *)"]


def test_hooks_arrays_deep_additive(isolated_home: Path) -> None:
    home = Path(os.environ["HOME"])
    _write_json(
        home / ".claude" / "settings.json",
        {"hooks": {"PreToolUse": [{"command": "echo user"}]}},
    )
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"hooks": {"PreToolUse": [{"command": "echo project"}]}},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.hooks["PreToolUse"] == [
        {"command": "echo user"},
        {"command": "echo project"},
    ]


def test_env_var_override_model(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_json(isolated_home / ".claude" / "settings.json", {"model": "sonnet"})
    monkeypatch.setenv("ANTHROPIC_MODEL", "ollama/kimi-k2.6:cloud")
    s = load_mink_settings(cwd=isolated_home)
    assert s.model == "ollama/kimi-k2.6:cloud"


def test_env_var_override_base_url(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:11434")
    s = load_mink_settings(cwd=isolated_home)
    assert s.env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"


def test_invalid_json_raises_with_path_and_line(isolated_home: Path) -> None:
    bad = isolated_home / ".claude" / "settings.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('{"permissions": {\n  "allow": [,]\n}}')
    with pytest.raises(MinkSettingsError) as excinfo:
        load_mink_settings(cwd=isolated_home)
    msg = str(excinfo.value)
    assert "settings.json" in msg
    assert "line" in msg


def test_empty_file_is_treated_as_empty(isolated_home: Path) -> None:
    p = isolated_home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    s = load_mink_settings(cwd=isolated_home)
    assert s.model is None


def test_top_level_array_rejected(isolated_home: Path) -> None:
    p = isolated_home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[]")
    with pytest.raises(MinkSettingsError):
        load_mink_settings(cwd=isolated_home)


def test_to_chimera_loop_config_wires_permissions(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {
            "permissions": {
                "allow": ["Read"],
                "ask": ["Bash(git push *)"],
                "deny": ["WebFetch"],
            }
        },
    )
    s = load_mink_settings(cwd=isolated_home)
    cfg = s.to_chimera_loop_config()
    from chimera.permissions.base import PermissionAction
    from chimera.permissions.rule import PermissionRuleset

    assert isinstance(cfg.permissions, PermissionRuleset)
    # WebFetch should be denied (last-write-wins on a Tool-only pattern).
    assert cfg.permissions.evaluate("WebFetch", {}) is PermissionAction.DENY
    # Read should be allowed.
    assert cfg.permissions.evaluate("Read", {"path": "x"}) is PermissionAction.ALLOW


def test_camelcase_default_mode_accepted(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"permissions": {"defaultMode": "acceptEdits"}},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.permissions.default_mode == "acceptEdits"


def test_additional_directories_merged(isolated_home: Path) -> None:
    home = Path(os.environ["HOME"])
    _write_json(
        home / ".claude" / "settings.json",
        {"permissions": {"additionalDirectories": ["/tmp/a"]}},
    )
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"permissions": {"additionalDirectories": ["/tmp/b"]}},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.permissions.additional_directories == ["/tmp/a", "/tmp/b"]

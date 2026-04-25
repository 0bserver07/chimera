"""Tests for chimera.tools.config_tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.tools.config_tool import ConfigTool


@pytest.fixture
def scoped_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Redirect the user-scope home and project root into ``tmp_path``."""
    user_home = tmp_path / "home"
    project_root = tmp_path / "proj"
    user_home.mkdir()
    project_root.mkdir()
    monkeypatch.setenv("CHIMERA_SETTINGS_HOME", str(user_home))
    monkeypatch.setenv("CHIMERA_PROJECT_ROOT", str(project_root))
    return {
        "user": user_home / ".claude" / "settings.json",
        "project": project_root / ".claude" / "settings.json",
        "local": project_root / ".claude" / "settings.local.json",
    }


@pytest.mark.parametrize("scope", ["user", "project", "local"])
def test_set_get_round_trip(
    scoped_paths: dict[str, Path], scope: str
) -> None:
    tool = ConfigTool()
    res = tool.execute(
        {"action": "set", "scope": scope, "key": "model", "value": "kimi-k2.6:cloud"},
        env=None,
    )
    assert res.success, res.error
    assert scoped_paths[scope].exists()
    got = tool.execute(
        {"action": "get", "scope": scope, "key": "model"},
        env=None,
    )
    assert got.success
    assert json.loads(got.output) == "kimi-k2.6:cloud"


def test_list_returns_full_dict(scoped_paths: dict[str, Path]) -> None:
    tool = ConfigTool()
    tool.execute(
        {"action": "set", "scope": "project", "key": "model", "value": "m1"},
        env=None,
    )
    tool.execute(
        {
            "action": "set",
            "scope": "project",
            "key": "permissions.mode",
            "value": "ask",
        },
        env=None,
    )
    res = tool.execute({"action": "list", "scope": "project"}, env=None)
    assert res.success
    parsed = json.loads(res.output)
    assert parsed["model"] == "m1"
    assert parsed["permissions"]["mode"] == "ask"


def test_list_empty_when_missing(scoped_paths: dict[str, Path]) -> None:
    res = ConfigTool().execute({"action": "list", "scope": "user"}, env=None)
    assert res.success
    assert json.loads(res.output) == {}


def test_rejects_unknown_top_level_key(scoped_paths: dict[str, Path]) -> None:
    tool = ConfigTool()
    res = tool.execute(
        {"action": "set", "scope": "user", "key": "evil", "value": 1},
        env=None,
    )
    assert not res.success
    assert "documented schema" in (res.error or "")


def test_rejects_invalid_scope(scoped_paths: dict[str, Path]) -> None:
    res = ConfigTool().execute(
        {"action": "list", "scope": "global"},
        env=None,
    )
    assert not res.success
    assert "invalid scope" in (res.error or "")


def test_get_missing_key_returns_null(scoped_paths: dict[str, Path]) -> None:
    res = ConfigTool().execute(
        {"action": "get", "scope": "project", "key": "model"},
        env=None,
    )
    assert res.success
    assert json.loads(res.output) is None


def test_scopes_isolated(scoped_paths: dict[str, Path]) -> None:
    tool = ConfigTool()
    tool.execute(
        {"action": "set", "scope": "user", "key": "model", "value": "u"},
        env=None,
    )
    tool.execute(
        {"action": "set", "scope": "project", "key": "model", "value": "p"},
        env=None,
    )
    user_val = json.loads(
        tool.execute({"action": "get", "scope": "user", "key": "model"}, env=None).output
    )
    proj_val = json.loads(
        tool.execute(
            {"action": "get", "scope": "project", "key": "model"}, env=None
        ).output
    )
    assert user_val == "u"
    assert proj_val == "p"

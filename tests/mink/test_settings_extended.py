"""W13-G14 — verify the expanded ``settings.json`` schema parses CC keys.

The base loader (`tests.mink.test_mink_settings_loader`) covers
permissions, hooks, mcp, and env. This file adds coverage for the new
keys recognised by W13-G14:

* ``keybindings``
* ``outputStyles``
* ``statusline`` (bool / dict / legacy ``statuslineCommand`` string)
* ``theme``
* ``cleanupPeriodDays``
* ``includeCoAuthoredBy``
* ``forceLoginMethod``
* ``autoUpdates``
* ``verbose``
* ``installMethod``
* ``preferredNotifChannel``
* ``awsAuthRefresh``
* ``enabledMcpjsonServers`` / ``disabledMcpjsonServers`` (additive merge)

Backwards compatibility: tests for unmodified keys live in
``test_mink_settings_loader`` and are not duplicated here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from chimera.mink.settings import load_mink_settings


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for k in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OUTPUT_FORMAT",
    ):
        monkeypatch.delenv(k, raising=False)
    return project


# ---------------------------------------------------------------------------
# Defaults: missing keys fall back to documented defaults
# ---------------------------------------------------------------------------


def test_defaults_when_keys_absent(isolated_home: Path) -> None:
    s = load_mink_settings(cwd=isolated_home)
    assert s.keybindings == {}
    assert s.output_styles == {}
    assert s.statusline is None
    assert s.theme is None
    assert s.cleanup_period_days is None
    assert s.include_co_authored_by is True
    assert s.force_login_method is None
    assert s.auto_updates is True
    assert s.verbose is False
    assert s.install_method is None
    assert s.preferred_notif_channel is None
    assert s.aws_auth_refresh is None
    assert s.enabled_mcp_json_servers == []
    assert s.disabled_mcp_json_servers == []


# ---------------------------------------------------------------------------
# keybindings
# ---------------------------------------------------------------------------


def test_keybindings_loaded(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"keybindings": {"submit": "ctrl-d", "cancel": "ctrl-c"}},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.keybindings == {"submit": "ctrl-d", "cancel": "ctrl-c"}


def test_keybindings_drops_non_string_values(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"keybindings": {"submit": "ctrl-d", "bad": 42}},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.keybindings == {"submit": "ctrl-d"}


# ---------------------------------------------------------------------------
# outputStyles
# ---------------------------------------------------------------------------


def test_output_styles_loaded(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {
            "outputStyles": {
                "default": {"theme": "monokai", "max_width": 120},
                "compact": {"theme": "minimal"},
            },
        },
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.output_styles["default"]["theme"] == "monokai"
    assert s.output_styles["default"]["max_width"] == 120
    assert s.output_styles["compact"] == {"theme": "minimal"}


# ---------------------------------------------------------------------------
# statusline (bool / dict / legacy string)
# ---------------------------------------------------------------------------


def test_statusline_bool_disabled(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"statusline": False},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.statusline is False


def test_statusline_bool_enabled(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"statusline": True},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.statusline is True


def test_statusline_dict_with_command(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"statusline": {"command": "scripts/status.sh", "format": "{model}"}},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert isinstance(s.statusline, dict)
    assert s.statusline["command"] == "scripts/status.sh"
    assert s.statusline["format"] == "{model}"


def test_statusline_legacy_string_promotes_to_dict(isolated_home: Path) -> None:
    """CC older releases set ``statuslineCommand`` to a bare string."""
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"statuslineCommand": "scripts/legacy-status.sh"},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.statusline == {
        "command": "scripts/legacy-status.sh",
        "enabled": True,
    }


# ---------------------------------------------------------------------------
# theme + scalar keys
# ---------------------------------------------------------------------------


def test_theme_loaded(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json", {"theme": "dark"},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.theme == "dark"


def test_misc_scalar_keys(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {
            "cleanupPeriodDays": 14,
            "includeCoAuthoredBy": False,
            "forceLoginMethod": "oauth",
            "autoUpdates": False,
            "verbose": True,
            "installMethod": "brew",
            "preferredNotifChannel": "system",
            "awsAuthRefresh": "scripts/refresh-aws.sh",
        },
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.cleanup_period_days == 14
    assert s.include_co_authored_by is False
    assert s.force_login_method == "oauth"
    assert s.auto_updates is False
    assert s.verbose is True
    assert s.install_method == "brew"
    assert s.preferred_notif_channel == "system"
    assert s.aws_auth_refresh == "scripts/refresh-aws.sh"


# ---------------------------------------------------------------------------
# enabled / disabled MCP JSON servers — additive merge across layers
# ---------------------------------------------------------------------------


def test_mcp_json_server_lists_loaded(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {
            "enabledMcpjsonServers": ["alpha", "beta"],
            "disabledMcpjsonServers": ["legacy"],
        },
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.enabled_mcp_json_servers == ["alpha", "beta"]
    assert s.disabled_mcp_json_servers == ["legacy"]


def test_mcp_json_server_lists_concat_dedupe_across_layers(
    isolated_home: Path,
) -> None:
    home = Path(os.environ["HOME"])
    _write_json(
        home / ".claude" / "settings.json",
        {"enabledMcpjsonServers": ["alpha"]},
    )
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {"enabledMcpjsonServers": ["alpha", "beta"]},  # alpha duplicated
    )
    s = load_mink_settings(cwd=isolated_home)
    # Concat-deduped: "alpha" not duplicated, "beta" appended.
    assert s.enabled_mcp_json_servers == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Layer precedence: project overrides user for the new scalars
# ---------------------------------------------------------------------------


def test_project_overrides_user_for_theme(isolated_home: Path) -> None:
    home = Path(os.environ["HOME"])
    _write_json(home / ".claude" / "settings.json", {"theme": "light"})
    _write_json(
        isolated_home / ".claude" / "settings.json", {"theme": "dark"},
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.theme == "dark"


# ---------------------------------------------------------------------------
# Backwards compat: existing keys still parse
# ---------------------------------------------------------------------------


def test_existing_keys_unaffected(isolated_home: Path) -> None:
    _write_json(
        isolated_home / ".claude" / "settings.json",
        {
            "model": "opus",
            "permissions": {"allow": ["Read"], "deny": ["Bash"]},
            "theme": "dark",  # new key alongside old
        },
    )
    s = load_mink_settings(cwd=isolated_home)
    assert s.model == "opus"
    assert s.permissions.allow == ["Read"]
    assert s.permissions.deny == ["Bash"]
    assert s.theme == "dark"

"""Tests for the ``chimera config`` subcommand and config_loader helper.

The fixture redirects ``$HOME`` to ``tmp_path`` so tests never touch the real
``~/.chimera/config.toml`` on the developer's machine.
"""
from __future__ import annotations

import io
import os
import tomllib
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from chimera.cli import config_cmd
from chimera.cli.config_loader import (
    config_path,
    load_config,
    resolve_default,
)
from chimera.cli.main import build_parser


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect $HOME (and clear $CHIMERA_CONFIG_HOME) to a temp dir.

    Returns the path that ``config_path()`` will resolve to inside the test.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CHIMERA_CONFIG_HOME", raising=False)
    return tmp_path / ".chimera" / "config.toml"


def _run(argv: list[str]) -> tuple[int, str]:
    """Invoke the top-level CLI parser, capturing stdout."""
    parser = build_parser()
    args = parser.parse_args(argv)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = config_cmd.run(args)
    return rc, buf.getvalue()


# ---------------------------------------------------------------------------
# config set / get / unset
# ---------------------------------------------------------------------------


def test_set_and_get_string(fake_home: Path) -> None:
    rc, _ = _run(["config", "set", "otter.model", "glm-5"])
    assert rc == 0
    assert fake_home.exists()

    rc, out = _run(["config", "get", "otter.model"])
    assert rc == 0
    assert out.strip() == "glm-5"


def test_set_int_and_bool_round_trip(fake_home: Path) -> None:
    rc, _ = _run(["config", "set", "shrew.vram-gb", "16"])
    assert rc == 0
    rc, _ = _run(["config", "set", "global.no-color", "true"])
    assert rc == 0

    # Underlying TOML must be typed correctly, not stringified.
    parsed = tomllib.loads(fake_home.read_text())
    assert parsed["shrew"]["vram-gb"] == 16
    assert parsed["global"]["no-color"] is True

    rc, out = _run(["config", "get", "shrew.vram-gb"])
    assert rc == 0 and out.strip() == "16"

    rc, out = _run(["config", "get", "global.no-color"])
    assert rc == 0 and out.strip() == "true"


def test_get_missing_key_prints_blank(fake_home: Path) -> None:
    rc, out = _run(["config", "get", "ferret.does-not-exist"])
    assert rc == 0
    assert out == "\n"


def test_bare_key_lands_in_global(fake_home: Path) -> None:
    rc, _ = _run(["config", "set", "no-color", "true"])
    assert rc == 0

    parsed = tomllib.loads(fake_home.read_text())
    assert parsed == {"global": {"no-color": True}}

    rc, out = _run(["config", "get", "no-color"])
    assert rc == 0 and out.strip() == "true"


def test_unset_removes_key_and_empty_table(fake_home: Path) -> None:
    _run(["config", "set", "weasel.approval", "auto"])
    rc, _ = _run(["config", "unset", "weasel.approval"])
    assert rc == 0

    parsed = tomllib.loads(fake_home.read_text())
    assert parsed == {}


def test_unset_missing_is_noop(fake_home: Path) -> None:
    rc, out = _run(["config", "unset", "mink.nope"])
    assert rc == 0
    assert "not set" in out


# ---------------------------------------------------------------------------
# config list
# ---------------------------------------------------------------------------


def test_list_filters_by_cli(fake_home: Path) -> None:
    _run(["config", "set", "otter.model", "glm-5"])
    _run(["config", "set", "mink.permission-mode", "auto"])
    _run(["config", "set", "global.no-color", "true"])

    rc, out = _run(["config", "list", "--cli", "otter"])
    assert rc == 0
    assert "otter.model" in out
    assert "mink." not in out
    assert "global." not in out

    rc, out = _run(["config", "list"])
    assert rc == 0
    assert "otter.model" in out
    assert "mink.permission-mode" in out
    assert "global.no-color" in out


def test_list_empty(fake_home: Path) -> None:
    rc, out = _run(["config", "list"])
    assert rc == 0
    assert "no defaults" in out


# ---------------------------------------------------------------------------
# config edit
# ---------------------------------------------------------------------------


def test_edit_skipped_without_editor(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EDITOR", raising=False)
    parser = build_parser()
    args = parser.parse_args(["config", "edit"])
    rc = config_cmd.run(args)
    assert rc == 2


def test_edit_invokes_editor(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test the edit path with a fake $EDITOR script that just touches a marker."""
    if os.name == "nt":  # pragma: no cover - posix-only marker script
        pytest.skip("posix-only fake editor script")

    marker = tmp_path / "editor-was-run"
    fake_editor = tmp_path / "fake-editor.sh"
    fake_editor.write_text(
        "#!/bin/sh\n"
        f'touch "{marker}"\n'
        'exit 0\n'
    )
    fake_editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(fake_editor))

    parser = build_parser()
    args = parser.parse_args(["config", "edit"])
    rc = config_cmd.run(args)
    assert rc == 0
    assert marker.exists()
    # The config file is touched into existence so the editor has something
    # to open even on a fresh machine.
    assert fake_home.exists()


# ---------------------------------------------------------------------------
# config_loader.resolve_default
# ---------------------------------------------------------------------------


def test_resolve_default_uses_table(fake_home: Path) -> None:
    _run(["config", "set", "otter.model", "glm-5"])
    assert resolve_default("otter", "model", "claude-sonnet-4") == "glm-5"


def test_resolve_default_falls_back_to_global(fake_home: Path) -> None:
    _run(["config", "set", "global.no-color", "true"])
    # otter table has no no-color key, so resolution falls through to global.
    assert resolve_default("otter", "no-color", False) is True


def test_resolve_default_returns_fallback_when_unset(fake_home: Path) -> None:
    assert resolve_default("ferret", "approval", "ask") == "ask"


def test_config_path_respects_chimera_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "alt-home"
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(override))
    expected = override / "config.toml"
    assert config_path() == expected


def test_load_config_swallows_corrupt_file(fake_home: Path) -> None:
    fake_home.parent.mkdir(parents=True, exist_ok=True)
    fake_home.write_text("this is not [valid toml = =")
    assert load_config() == {}


# ---------------------------------------------------------------------------
# Key-parsing edge cases
# ---------------------------------------------------------------------------


def test_invalid_key_set_returns_2(fake_home: Path) -> None:
    rc, _ = _run(["config", "set", ".bad", "x"])
    assert rc == 2

    rc, _ = _run(["config", "set", "bad.", "x"])
    assert rc == 2

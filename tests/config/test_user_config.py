"""The unified user/TUI config loader (T13 / #173).

Pins the one loader that replaced the TOML-vs-YAML dialect split: both the
old ``config.toml`` keybinds AND the old ``config.{yaml,yml,json}`` statusline
files must still load, one documented precedence chain, and byte-identical
behavior when no config is present.
"""
import json

from chimera.config import user_config


def _write(scope, name, text):
    scope.mkdir(parents=True, exist_ok=True)
    (scope / name).write_text(text, encoding="utf-8")


# -- backward compatibility: both dialects still load ------------------------
def test_old_toml_keybinds_still_load(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path))
    _write(tmp_path, "config.toml", '[tui.keybinds]\ntoggle_expand = "ctrl+u"\n')
    cfg = user_config.load_user_scope_config()
    assert cfg["tui"]["keybinds"] == {"toggle_expand": "ctrl+u"}


def test_old_json_statusline_still_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path))
    _write(tmp_path, "config.json",
           json.dumps({"tui": {"status_line": ["model", "cost"]}}))
    cfg = user_config.load_user_scope_config()
    assert cfg["tui"]["status_line"] == ["model", "cost"]


def test_both_dialects_in_one_scope_deep_merge(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path))
    _write(tmp_path, "config.toml", '[tui.keybinds]\ntoggle_expand = "ctrl+u"\n')
    _write(tmp_path, "config.json",
           json.dumps({"tui": {"status_line": ["model"]}}))
    tui = user_config.load_user_scope_config()["tui"]
    # keybinds (toml) and status_line (json) coexist — the deep merge does not
    # let one file's tui table clobber the other's.
    assert tui["keybinds"] == {"toggle_expand": "ctrl+u"}
    assert tui["status_line"] == ["model"]


def test_toml_wins_key_collision_within_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path))
    _write(tmp_path, "config.json", json.dumps({"tui": {"theme": "from-json"}}))
    _write(tmp_path, "config.toml", '[tui]\ntheme = "from-toml"\n')
    assert user_config.load_user_scope_config()["tui"]["theme"] == "from-toml"


# -- precedence across scopes ------------------------------------------------
def test_project_scope_overrides_user_scope(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    _write(home / ".chimera", "config.toml", '[tui]\ntheme = "user"\n')
    _write(project / ".chimera", "config.toml", '[tui]\ntheme = "project"\n')
    tui = user_config.load_tui_config(project, home=home)
    assert tui["theme"] == "project"


def test_xdg_is_lowest_precedence(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    _write(home / ".config" / "chimera", "config.toml", '[tui]\ntheme = "xdg"\n')
    _write(home / ".chimera", "config.toml", '[tui]\ntheme = "user"\n')
    tui = user_config.load_tui_config(project, home=home)
    assert tui["theme"] == "user"  # user scope beats xdg


# -- default behavior: nothing present, nothing surprising -------------------
def test_absent_config_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path / "nope"))
    assert user_config.load_user_scope_config() == {}
    assert user_config.load_tui_config(tmp_path, home=tmp_path) == {}


def test_broken_config_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(tmp_path))
    _write(tmp_path, "config.toml", "this is [not valid toml")
    # A broken file degrades to {} — startup is never blocked.
    assert user_config.load_user_scope_config() == {}


def test_env_home_overrides_home_arg_for_user_scope(tmp_path, monkeypatch):
    override = tmp_path / "override"
    _write(override, "config.toml", '[tui]\ntheme = "env"\n')
    monkeypatch.setenv("CHIMERA_CONFIG_HOME", str(override))
    # load_user_scope_config honors $CHIMERA_CONFIG_HOME over the real home.
    assert user_config.load_user_scope_config()["tui"]["theme"] == "env"

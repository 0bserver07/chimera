"""Tests for :mod:`chimera.ferret.config` — TOML config ingest.

Covers:

* Empty / missing files return empty dicts (defensive contract).
* User-scope and project-scope configs load independently.
* ``merge_configs`` performs a shallow-recursive merge with project
  overriding user.
* ``load_config`` end-to-end materializes a :class:`FerretConfig` with
  user + project paths recorded on the result.
* ``--config FILE`` style explicit override replaces both defaults.
* Malformed TOML emits a stderr warning and returns ``{}``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.ferret import config as config_mod


# ---------------------------------------------------------------------------
# Stdlib tomllib loader
# ---------------------------------------------------------------------------


def test_load_tomllib_returns_module() -> None:
    """``_load_tomllib`` returns a real ``tomllib`` module on Python 3.11+."""
    mod = config_mod._load_tomllib()  # noqa: SLF001
    assert hasattr(mod, "loads")
    assert hasattr(mod, "load")


# ---------------------------------------------------------------------------
# load_user_config / load_project_config — file system fixtures
# ---------------------------------------------------------------------------


def test_load_user_config_missing_returns_empty(tmp_path: Path) -> None:
    """Missing user config returns ``{}`` without raising."""
    missing = tmp_path / "absent" / "config.toml"
    assert not missing.exists()
    out = config_mod.load_user_config(missing)
    assert out == {}


def test_load_user_config_reads_simple_toml(tmp_path: Path) -> None:
    """A real TOML file is parsed into a dict."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[provider]\nname = "openai"\nmodel = "gpt-5"\n', encoding="utf-8",
    )
    out = config_mod.load_user_config(cfg)
    assert out == {"provider": {"name": "openai", "model": "gpt-5"}}


def test_load_user_config_expands_tilde(monkeypatch, tmp_path: Path) -> None:
    """``~`` in the path is expanded via :func:`os.path.expanduser`."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    codex_dir = home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        'foo = "bar"\n', encoding="utf-8",
    )
    out = config_mod.load_user_config("~/.codex/config.toml")
    assert out == {"foo": "bar"}


def test_load_project_config_missing_returns_empty(tmp_path: Path) -> None:
    """Missing project config returns ``{}``."""
    out = config_mod.load_project_config(tmp_path)
    assert out == {}


def test_load_project_config_reads_relative(tmp_path: Path) -> None:
    """``.codex/config.toml`` under project_root is parsed."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        '[sandbox]\nmode = "workspace-write"\n', encoding="utf-8",
    )
    out = config_mod.load_project_config(tmp_path)
    assert out == {"sandbox": {"mode": "workspace-write"}}


def test_load_project_config_default_uses_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When project_root is None, falls back to ``os.getcwd()``."""
    monkeypatch.chdir(tmp_path)
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        'cwd_test = true\n', encoding="utf-8",
    )
    out = config_mod.load_project_config()
    assert out == {"cwd_test": True}


# ---------------------------------------------------------------------------
# merge_configs
# ---------------------------------------------------------------------------


def test_merge_configs_empty_inputs() -> None:
    assert config_mod.merge_configs({}, {}) == {}


def test_merge_configs_disjoint_keys() -> None:
    user = {"a": 1, "b": 2}
    project = {"c": 3}
    assert config_mod.merge_configs(user, project) == {"a": 1, "b": 2, "c": 3}


def test_merge_configs_project_overrides_user() -> None:
    """Top-level scalar values: project wins."""
    user = {"model": "gpt-4o"}
    project = {"model": "gpt-5"}
    out = config_mod.merge_configs(user, project)
    assert out == {"model": "gpt-5"}


def test_merge_configs_recursive_dict() -> None:
    """Nested dicts merge with project keys overriding user keys."""
    user = {
        "provider": {"name": "openai", "model": "gpt-4o", "tokens": 1000},
    }
    project = {
        "provider": {"model": "gpt-5"},
    }
    out = config_mod.merge_configs(user, project)
    assert out == {
        "provider": {"name": "openai", "model": "gpt-5", "tokens": 1000},
    }


def test_merge_configs_does_not_mutate_inputs() -> None:
    """``merge_configs`` returns a fresh dict; inputs are unchanged."""
    user = {"a": {"b": 1}}
    project = {"a": {"b": 2}}
    out = config_mod.merge_configs(user, project)
    assert user == {"a": {"b": 1}}
    assert project == {"a": {"b": 2}}
    assert out == {"a": {"b": 2}}


def test_merge_configs_project_dict_replaces_user_scalar() -> None:
    """When user has a scalar and project has a dict, project still wins."""
    user = {"x": 42}
    project = {"x": {"nested": True}}
    out = config_mod.merge_configs(user, project)
    assert out == {"x": {"nested": True}}


# ---------------------------------------------------------------------------
# load_config end-to-end
# ---------------------------------------------------------------------------


def test_load_config_returns_ferret_config_subclass(tmp_path: Path) -> None:
    """``load_config`` returns a :class:`FerretConfig` instance."""
    cfg = config_mod.load_config(tmp_path, user_path=tmp_path / "no-user.toml")
    assert isinstance(cfg, config_mod.FerretConfig)
    assert isinstance(cfg, dict)


def test_load_config_merges_user_and_project(tmp_path: Path) -> None:
    """Both files are read and merged with project winning."""
    user_cfg = tmp_path / "user.toml"
    user_cfg.write_text(
        '[provider]\nname = "openai"\nmodel = "gpt-4o"\n', encoding="utf-8",
    )
    project_dir = tmp_path / "project"
    (project_dir / ".codex").mkdir(parents=True)
    (project_dir / ".codex" / "config.toml").write_text(
        '[provider]\nmodel = "gpt-5"\n', encoding="utf-8",
    )

    cfg = config_mod.load_config(project_dir, user_path=user_cfg)
    # Strip bookkeeping keys for the value assertion.
    plain = {
        k: v for k, v in cfg.items()
        if not k.startswith("__")
    }
    assert plain == {
        "provider": {"name": "openai", "model": "gpt-5"},
    }


def test_load_config_records_paths(tmp_path: Path) -> None:
    """``__user_path__`` and ``__project_path__`` reflect what was read."""
    user_cfg = tmp_path / "user.toml"
    user_cfg.write_text("a = 1\n", encoding="utf-8")
    project_dir = tmp_path / "project"
    (project_dir / ".codex").mkdir(parents=True)
    project_cfg = project_dir / ".codex" / "config.toml"
    project_cfg.write_text("b = 2\n", encoding="utf-8")

    cfg = config_mod.load_config(project_dir, user_path=user_cfg)
    assert cfg.user_path == user_cfg.resolve()
    assert cfg.project_path == project_cfg


def test_load_config_explicit_path_replaces_defaults(tmp_path: Path) -> None:
    """``explicit_path`` short-circuits user + project ingest."""
    explicit = tmp_path / "override.toml"
    explicit.write_text("only = true\n", encoding="utf-8")

    cfg = config_mod.load_config(
        tmp_path, explicit_path=explicit,
    )
    plain = {k: v for k, v in cfg.items() if not k.startswith("__")}
    assert plain == {"only": True}
    assert cfg.project_path == explicit.resolve()
    assert cfg.user_path is None


def test_load_config_no_files_returns_empty(tmp_path: Path) -> None:
    """When neither file exists, the merged config is empty."""
    cfg = config_mod.load_config(
        tmp_path, user_path=tmp_path / "missing.toml",
    )
    plain = {k: v for k, v in cfg.items() if not k.startswith("__")}
    assert plain == {}
    assert cfg.user_path is None
    assert cfg.project_path is None


def test_load_config_malformed_toml_returns_empty_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed TOML emits a stderr warning and returns ``{}``."""
    bad = tmp_path / "bad.toml"
    bad.write_text("this is not toml = = =\n[oops\n", encoding="utf-8")
    cfg = config_mod.load_config(
        tmp_path, user_path=bad,
    )
    plain = {k: v for k, v in cfg.items() if not k.startswith("__")}
    assert plain == {}
    err = capsys.readouterr().err
    assert "ferret" in err.lower()
    assert "config read failed" in err.lower()


# ---------------------------------------------------------------------------
# Filesystem fact: default paths
# ---------------------------------------------------------------------------


def test_default_user_config_path_is_codex_toml() -> None:
    """The default user path is the upstream-compatible ``~/.codex/config.toml``."""
    assert config_mod.DEFAULT_USER_CONFIG_PATH == "~/.codex/config.toml"


def test_default_project_config_name_is_codex_toml() -> None:
    """The default project name matches the upstream layout."""
    assert config_mod.DEFAULT_PROJECT_CONFIG_NAME == ".codex/config.toml"

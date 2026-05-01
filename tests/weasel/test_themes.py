"""Tests for ``chimera.weasel.themes`` — built-in + on-disk theme registry.

Exercises the loader against synthetic theme directories materialized
under ``tmp_path``. Mirrors the extensions test layout: each test names
exactly the surface it asserts on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.weasel.themes import (
    Theme,
    get_theme,
    load_themes,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Return a fresh project root under tmp_path."""
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def user_root(tmp_path: Path) -> Path:
    """Return a fresh user theme root under tmp_path."""
    root = tmp_path / "user-themes"
    root.mkdir()
    return root


def _write_theme(
    root: Path,
    filename: str,
    payload: dict[str, object],
) -> Path:
    """Write ``payload`` as a theme JSON file under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    target = root / filename
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Built-in themes
# ---------------------------------------------------------------------------


def test_load_themes_includes_three_builtins(
    project_root: Path,
    user_root: Path,
) -> None:
    """``default``, ``dark``, and ``solarized`` are always present."""
    registry = load_themes(project_root, user_root=user_root)
    assert "default" in registry
    assert "dark" in registry
    assert "solarized" in registry


def test_builtin_default_carries_required_color_slots(
    project_root: Path,
    user_root: Path,
) -> None:
    """Default theme exposes the conventional palette slots."""
    registry = load_themes(project_root, user_root=user_root)
    default = registry["default"]
    for slot in ("foreground", "background", "accent"):
        assert slot in default.colors
        assert default.colors[slot]


def test_builtin_themes_carry_style_prompts(
    project_root: Path,
    user_root: Path,
) -> None:
    """Every built-in theme defines at least the ``user`` prompt slot."""
    registry = load_themes(project_root, user_root=user_root)
    for name in ("default", "dark", "solarized"):
        assert "user" in registry[name].style_prompts


# ---------------------------------------------------------------------------
# get_theme lookup
# ---------------------------------------------------------------------------


def test_get_theme_returns_named_when_present() -> None:
    """``get_theme('dark')`` returns the dark built-in by name."""
    assert get_theme("dark").name == "dark"


def test_get_theme_falls_back_to_default_for_unknown_name() -> None:
    """Unknown names fall back to ``default`` rather than raising."""
    theme = get_theme("nope-not-a-theme")
    assert theme.name == "default"


def test_get_theme_falls_back_to_default_for_none() -> None:
    """``None`` resolves to ``default`` so flagless calls work."""
    assert get_theme(None).name == "default"


def test_get_theme_strips_whitespace() -> None:
    """Whitespace-padded names resolve correctly."""
    assert get_theme("  solarized  ").name == "solarized"


def test_get_theme_falls_back_for_empty_or_whitespace() -> None:
    """Empty / whitespace-only names land on default."""
    assert get_theme("").name == "default"
    assert get_theme("   ").name == "default"


def test_get_theme_uses_supplied_registry() -> None:
    """Explicit registries override the module-level cache."""
    custom = {"default": Theme(name="default", colors={"foreground": "#fff"})}
    assert get_theme("default", registry=custom).colors == {"foreground": "#fff"}


# ---------------------------------------------------------------------------
# On-disk discovery
# ---------------------------------------------------------------------------


def test_user_scope_theme_is_loaded(
    project_root: Path,
    user_root: Path,
) -> None:
    """Themes under user_root land in the registry."""
    _write_theme(
        user_root,
        "midnight.json",
        {
            "name": "midnight",
            "colors": {"foreground": "#eee", "background": "#000"},
            "style_prompts": {"user": "midnight> "},
        },
    )
    registry = load_themes(project_root, user_root=user_root)
    assert "midnight" in registry
    midnight = registry["midnight"]
    assert midnight.colors["foreground"] == "#eee"
    assert midnight.style_prompts["user"] == "midnight> "


def test_project_scope_theme_is_loaded(
    project_root: Path,
    user_root: Path,
) -> None:
    """Themes under <project>/.weasel/themes/ land in the registry."""
    _write_theme(
        project_root / ".weasel" / "themes",
        "ocean.json",
        {"name": "ocean", "colors": {"accent": "#005f87"}},
    )
    registry = load_themes(project_root, user_root=user_root)
    assert "ocean" in registry
    assert registry["ocean"].colors["accent"] == "#005f87"


def test_project_scope_overrides_user_scope(
    project_root: Path,
    user_root: Path,
) -> None:
    """On name collision the project entry wins."""
    _write_theme(
        user_root,
        "shared.json",
        {"name": "shared", "colors": {"accent": "user"}},
    )
    _write_theme(
        project_root / ".weasel" / "themes",
        "shared.json",
        {"name": "shared", "colors": {"accent": "project"}},
    )
    registry = load_themes(project_root, user_root=user_root)
    assert registry["shared"].colors["accent"] == "project"


def test_project_scope_overrides_builtin(
    project_root: Path,
    user_root: Path,
) -> None:
    """A project theme named ``default`` overrides the built-in."""
    _write_theme(
        project_root / ".weasel" / "themes",
        "default.json",
        {"name": "default", "colors": {"accent": "custom"}},
    )
    registry = load_themes(project_root, user_root=user_root)
    assert registry["default"].colors["accent"] == "custom"


def test_filename_stem_is_used_when_name_missing(
    project_root: Path,
    user_root: Path,
) -> None:
    """Themes without an explicit name fall back to the file stem."""
    _write_theme(
        user_root,
        "amber.json",
        {"colors": {"accent": "#ffaa00"}},
    )
    registry = load_themes(project_root, user_root=user_root)
    assert "amber" in registry
    assert registry["amber"].colors["accent"] == "#ffaa00"


def test_malformed_json_is_skipped(
    project_root: Path,
    user_root: Path,
) -> None:
    """A file that fails to parse does not break discovery."""
    user_root.mkdir(parents=True, exist_ok=True)
    (user_root / "broken.json").write_text("{not json", encoding="utf-8")
    _write_theme(user_root, "good.json", {"name": "good"})

    registry = load_themes(project_root, user_root=user_root)
    assert "good" in registry
    assert "broken" not in registry
    # built-ins still present.
    assert "default" in registry


def test_non_json_files_are_ignored(
    project_root: Path,
    user_root: Path,
) -> None:
    """Markdown/text files in the themes dir are skipped."""
    user_root.mkdir(parents=True, exist_ok=True)
    (user_root / "README.md").write_text("not a theme", encoding="utf-8")
    registry = load_themes(project_root, user_root=user_root)
    assert "README" not in registry


def test_hidden_files_are_skipped(
    project_root: Path,
    user_root: Path,
) -> None:
    """Dotfiles in the themes dir are skipped."""
    user_root.mkdir(parents=True, exist_ok=True)
    (user_root / ".hidden.json").write_text(
        json.dumps({"name": "hidden"}), encoding="utf-8"
    )
    registry = load_themes(project_root, user_root=user_root)
    assert "hidden" not in registry


def test_load_themes_returns_fresh_dict(
    project_root: Path,
    user_root: Path,
) -> None:
    """Mutating the returned dict does not poison subsequent calls."""
    first = load_themes(project_root, user_root=user_root)
    first["default"] = Theme(name="default", colors={"foreground": "mutated"})
    second = load_themes(project_root, user_root=user_root)
    assert second["default"].colors.get("foreground") != "mutated"


def test_get_theme_with_loaded_registry(
    project_root: Path,
    user_root: Path,
) -> None:
    """``get_theme`` honors the user-supplied loaded registry."""
    _write_theme(
        user_root,
        "neon.json",
        {"name": "neon", "colors": {"accent": "#39ff14"}},
    )
    registry = load_themes(project_root, user_root=user_root)
    assert get_theme("neon", registry=registry).colors["accent"] == "#39ff14"
    # Unknown still falls back to default.
    assert get_theme("missing", registry=registry).name == "default"

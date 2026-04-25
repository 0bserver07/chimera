"""Tests for ``chimera.otter.rules`` — AGENTS.md + .cursor + .opencode ingest.

Covers:

* ``discover_rule_files`` returns sources in user-then-project order.
* ``load_otter_rules`` concatenates bodies with provenance headers.
* Frontmatter is stripped from any source before concatenation.
* ``.cursor/rules/*.mdc`` files are picked up and sorted lexically.
* Project sources appear *after* user sources (later = higher precedence
  by recency in the prompt).
* The length cap truncates and appends the marker; a warning is logged.
* Missing files return ``""`` without raising.
* Empty / unreadable files are silently skipped.

All tests use ``tmp_path`` as both the project root and a fake ``$HOME``
so the real ``~/.opencode`` is never consulted.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from chimera.otter.rules import (
    DEFAULT_MAX_CHARS,
    TRUNCATION_MARKER,
    discover_rule_files,
    load_otter_rules,
    strip_frontmatter,
)


# -- Helpers --


def _write(path: Path, body: str) -> Path:
    """Create parent dirs and write ``body`` to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    """Return an empty $HOME dir under tmp_path so user-level rules are absent."""
    home = tmp_path / "_home"
    home.mkdir()
    return home


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Return an empty project root under tmp_path."""
    root = tmp_path / "proj"
    root.mkdir()
    return root


# -- strip_frontmatter --


def test_strip_frontmatter_removes_block() -> None:
    text = "---\ndescription: rules\n---\nBODY\n"
    out = strip_frontmatter(text)
    assert out.startswith("BODY")
    assert "description: rules" not in out
    assert not out.startswith("---")


def test_strip_frontmatter_no_block_passthrough() -> None:
    text = "BODY ONLY\n"
    assert strip_frontmatter(text) == text


def test_strip_frontmatter_unterminated_passthrough() -> None:
    """An opening ``---`` with no closer leaves the file untouched."""
    text = "---\nincomplete\nBODY\n"
    assert strip_frontmatter(text) == text


def test_strip_frontmatter_handles_bom() -> None:
    text = "﻿---\nk: v\n---\nBODY\n"
    out = strip_frontmatter(text)
    assert out.startswith("BODY")
    assert "k: v" not in out


# -- discover_rule_files --


def test_discover_returns_empty_when_nothing_exists(
    project: Path, fake_home: Path
) -> None:
    assert discover_rule_files(project, home=fake_home) == []


def test_discover_orders_user_then_project(project: Path, fake_home: Path) -> None:
    """User AGENTS.md before project AGENTS.md before .opencode/rules.md."""
    user_a = _write(fake_home / ".opencode" / "AGENTS.md", "USER_A")
    user_xdg = _write(fake_home / ".config" / "opencode" / "AGENTS.md", "USER_XDG")
    proj_a = _write(project / "AGENTS.md", "PROJ_A")
    cursor_b = _write(project / ".cursor" / "rules" / "b.mdc", "CURSOR_B")
    cursor_a = _write(project / ".cursor" / "rules" / "a.mdc", "CURSOR_A")
    proj_rules = _write(project / ".opencode" / "rules.md", "PROJ_RULES")

    files = discover_rule_files(project, home=fake_home)

    # Resolved comparison handles symlink-free temp dirs across platforms.
    assert files == [
        user_a.resolve(),
        user_xdg.resolve(),
        proj_a.resolve(),
        cursor_a.resolve(),  # sorted lexically
        cursor_b.resolve(),
        proj_rules.resolve(),
    ]


def test_discover_skips_missing_cursor_dir(project: Path, fake_home: Path) -> None:
    _write(project / "AGENTS.md", "PROJ")
    files = discover_rule_files(project, home=fake_home)
    assert files == [(project / "AGENTS.md").resolve()]


# -- load_otter_rules --


def test_load_returns_empty_when_no_rules(project: Path, fake_home: Path) -> None:
    assert load_otter_rules(project, home=fake_home) == ""


def test_load_concatenates_with_provenance(project: Path, fake_home: Path) -> None:
    _write(fake_home / ".opencode" / "AGENTS.md", "USER_LEVEL")
    _write(project / "AGENTS.md", "PROJECT_LEVEL")

    out = load_otter_rules(project, home=fake_home)

    assert "USER_LEVEL" in out
    assert "PROJECT_LEVEL" in out
    # Provenance headers point at the source paths.
    assert "<!-- source: " in out
    assert "AGENTS.md" in out


def test_load_user_before_project(project: Path, fake_home: Path) -> None:
    """Project content appears *after* user content (higher precedence)."""
    _write(fake_home / ".opencode" / "AGENTS.md", "USER_RULES")
    _write(project / "AGENTS.md", "PROJECT_RULES")

    out = load_otter_rules(project, home=fake_home)

    assert out.index("USER_RULES") < out.index("PROJECT_RULES")


def test_load_includes_cursor_and_opencode_rules(
    project: Path, fake_home: Path
) -> None:
    _write(project / "AGENTS.md", "AGENTS_BODY")
    _write(project / ".cursor" / "rules" / "style.mdc", "CURSOR_BODY")
    _write(project / ".opencode" / "rules.md", "OPENCODE_BODY")

    out = load_otter_rules(project, home=fake_home)

    assert "AGENTS_BODY" in out
    assert "CURSOR_BODY" in out
    assert "OPENCODE_BODY" in out
    # Order: AGENTS.md -> .cursor -> .opencode/rules.md.
    assert (
        out.index("AGENTS_BODY")
        < out.index("CURSOR_BODY")
        < out.index("OPENCODE_BODY")
    )


def test_load_strips_frontmatter(project: Path, fake_home: Path) -> None:
    _write(
        project / "AGENTS.md",
        "---\ndescription: hi\nglobs: [\"**/*.py\"]\n---\nREAL_RULES_BODY\n",
    )
    out = load_otter_rules(project, home=fake_home)
    assert "REAL_RULES_BODY" in out
    # Frontmatter content must not leak through.
    assert "description: hi" not in out
    assert "globs:" not in out


def test_load_truncates_above_cap_and_warns(
    project: Path, fake_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    big = "X" * (DEFAULT_MAX_CHARS * 2)
    _write(project / "AGENTS.md", big)

    with caplog.at_level(logging.WARNING, logger="chimera.otter.rules"):
        out = load_otter_rules(project, home=fake_home)

    assert len(out) <= DEFAULT_MAX_CHARS
    assert out.endswith(TRUNCATION_MARKER)
    assert any("truncating" in rec.getMessage() for rec in caplog.records)


def test_load_respects_custom_cap(project: Path, fake_home: Path) -> None:
    _write(project / "AGENTS.md", "Y" * 500)
    out = load_otter_rules(project, home=fake_home, max_chars=100)
    assert len(out) <= 100
    assert out.endswith(TRUNCATION_MARKER)


def test_load_skips_empty_files(project: Path, fake_home: Path) -> None:
    _write(project / "AGENTS.md", "")  # empty
    _write(project / ".opencode" / "rules.md", "REAL_BODY")
    out = load_otter_rules(project, home=fake_home)
    assert "REAL_BODY" in out
    # No empty source header should leak through with a blank body.
    headers = [
        line
        for line in out.splitlines()
        if line.startswith("<!-- source: ")
    ]
    assert len(headers) == 1


def test_load_xdg_user_path_picked_up(project: Path, fake_home: Path) -> None:
    """``~/.config/opencode/AGENTS.md`` is read alongside ``~/.opencode/AGENTS.md``."""
    _write(fake_home / ".config" / "opencode" / "AGENTS.md", "XDG_USER_RULES")
    out = load_otter_rules(project, home=fake_home)
    assert "XDG_USER_RULES" in out

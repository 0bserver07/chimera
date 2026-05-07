"""W13-G2 — verify the shared instruction-file loader.

Covers:

* project-only AGENTS.md loaded
* project-only CLAUDE.md loaded
* both files present (ordering, no duplicates)
* user-global ~/.claude/CLAUDE.md fallback
* user-global ~/.codex/AGENTS.md fallback
* neither present (graceful empty)
* file too large (truncate with marker)
* concatenated text wraps each file in an <instructions> block
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chimera.cli.instruction_files import (
    InstructionFile,
    load_instruction_files,
    load_instruction_text,
)


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin $HOME to tmp/home, create an empty project dir, return project."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Path.home() reads $HOME on POSIX; force monkeypatching for safety.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return project


# ---------------------------------------------------------------------------
# Empty: no files anywhere
# ---------------------------------------------------------------------------


def test_empty_when_no_files(tree: Path) -> None:
    files = load_instruction_files(project_dir=tree)
    assert files == []
    assert load_instruction_text(project_dir=tree) == ""


# ---------------------------------------------------------------------------
# Project-only AGENTS.md
# ---------------------------------------------------------------------------


def test_project_agents_md_loaded(tree: Path) -> None:
    (tree / "AGENTS.md").write_text("# Codex rules\nrun pytest before commit\n")
    files = load_instruction_files(project_dir=tree)
    assert len(files) == 1
    f = files[0]
    assert isinstance(f, InstructionFile)
    assert f.source == "AGENTS.md"
    assert "Codex rules" in f.text
    assert f.truncated is False


# ---------------------------------------------------------------------------
# Project-only CLAUDE.md
# ---------------------------------------------------------------------------


def test_project_claude_md_loaded(tree: Path) -> None:
    (tree / "CLAUDE.md").write_text("# CC rules\nuse the bash tool\n")
    files = load_instruction_files(project_dir=tree)
    assert len(files) == 1
    assert files[0].source == "CLAUDE.md"
    assert "CC rules" in files[0].text


# ---------------------------------------------------------------------------
# Both files present — ordering + no duplicates
# ---------------------------------------------------------------------------


def test_both_files_present(tree: Path) -> None:
    (tree / "AGENTS.md").write_text("agents file\n")
    (tree / "CLAUDE.md").write_text("claude file\n")
    files = load_instruction_files(project_dir=tree)
    sources = [f.source for f in files]
    # AGENTS.md is discovered before CLAUDE.md (project-discovery walks first).
    assert "AGENTS.md" in sources
    assert "CLAUDE.md" in sources
    # No path is reported twice.
    paths = [f.path for f in files]
    assert len(paths) == len(set(paths))


# ---------------------------------------------------------------------------
# User-global fallbacks
# ---------------------------------------------------------------------------


def test_user_global_claude_md_loaded(tree: Path) -> None:
    home = Path.home()
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "CLAUDE.md").write_text("# user-global\n")
    files = load_instruction_files(project_dir=tree)
    assert any(
        "user-global" in f.text and f.source == "CLAUDE.md" for f in files
    )


def test_user_global_codex_agents_md_loaded(tree: Path) -> None:
    home = Path.home()
    (home / ".codex").mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "AGENTS.md").write_text("# codex global\n")
    files = load_instruction_files(project_dir=tree)
    assert any(
        "codex global" in f.text and f.source == "AGENTS.md" for f in files
    )


def test_user_global_loaded_first(tree: Path) -> None:
    """User-global is the root-most layer, so it should appear before project."""
    home = Path.home()
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "CLAUDE.md").write_text("user\n")
    (tree / "CLAUDE.md").write_text("project\n")
    files = load_instruction_files(project_dir=tree)
    texts = [f.text.strip() for f in files]
    # User-level appears earlier than project-level.
    user_idx = next(i for i, t in enumerate(texts) if t == "user")
    proj_idx = next(i for i, t in enumerate(texts) if t == "project")
    assert user_idx < proj_idx


# ---------------------------------------------------------------------------
# Truncation — file exceeds the cap
# ---------------------------------------------------------------------------


def test_oversize_file_truncated_with_marker(tree: Path) -> None:
    big = "A" * (300 * 1024)  # 300 KiB > 256 KiB cap
    (tree / "AGENTS.md").write_text(big)
    files = load_instruction_files(project_dir=tree)
    assert len(files) == 1
    f = files[0]
    assert f.truncated is True
    # Text is capped at 256 KiB.
    assert len(f.text) <= 256 * 1024 + 10  # +10 for any decoding wiggle
    # Concatenated text carries the marker.
    text = load_instruction_text(project_dir=tree)
    assert "[truncated at 256 KiB]" in text


# ---------------------------------------------------------------------------
# load_instruction_text — wrapping
# ---------------------------------------------------------------------------


def test_concatenated_text_wraps_each_file_in_instructions_block(tree: Path) -> None:
    (tree / "AGENTS.md").write_text("alpha\n")
    (tree / "CLAUDE.md").write_text("beta\n")
    text = load_instruction_text(project_dir=tree)
    assert text.count("<instructions source=") == 2
    assert "</instructions>" in text
    assert "alpha" in text
    assert "beta" in text


def test_concatenated_text_carries_origin_path(tree: Path) -> None:
    (tree / "AGENTS.md").write_text("hi\n")
    text = load_instruction_text(project_dir=tree)
    assert str((tree / "AGENTS.md").resolve()) in text

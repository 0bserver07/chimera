"""The repo root is a guarded interface, not a scratch space.

Why this exists: one-off benchmark drivers (``pb_*.py``, ``scratch_*.py``)
accumulated at the repo root as the only loose Python files in the tree, six of
them listed in ``.gitignore`` *while tracked* (which asserts something false —
ignore rules only affect untracked files), and their run outputs piled up
1.3 GB of ``pb-runs/`` and ``runs/`` next to them. None of it was caught,
because nothing gated the root. These tests are that gate.

The disciplined homes, for the record:

* datasets      → ``~/.chimera/datasets`` (``chimera.eval.datasets.staging_dir``)
* results       → explicit ``--output`` files; curated receipts committed under
  ``data/`` deliberately — nothing in ``chimera/`` writes a cwd-relative dir
* run scratch   → temp dirs / sandboxes, or OUTSIDE the repo entirely
* one-off drivers → ``scripts/experiments/`` (see its README)

Adding a new top-level entry is a deliberate act: extend the allowlist here,
in the same commit, with a reason.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: Every top-level path that is ALLOWED to be tracked. Additions require
#: editing this set — that edit is the deliberate act the gate exists to force.
ALLOWED_ROOT_ENTRIES = frozenset({
    # meta / packaging
    ".github", ".gitignore", ".python-version",
    "pyproject.toml", "uv.lock",
    # top-level docs
    "CHANGELOG.md", "CLAUDE.md", "CODE_OF_CONDUCT.md", "CONTEXT.md",
    "CONTRIBUTING.md", "LICENSE", "README.md", "RELEASES.md", "RELEASING.md",
    "SECURITY.md",
    # the directories
    "chimera", "chimera-plugin", "data", "docs", "examples", "research",
    "scripts", "site", "tests",
})


def _tracked_root_entries() -> set[str] | None:
    """Top-level components of every tracked path, or ``None`` off-git."""
    try:
        proc = subprocess.run(
            ["git", "ls-files"], cwd=ROOT,
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
        return None
    if proc.returncode != 0:  # pragma: no cover - not a git checkout
        return None
    return {line.split("/", 1)[0] for line in proc.stdout.splitlines() if line}


def test_no_loose_python_files_at_the_repo_root() -> None:
    stray = sorted(p.name for p in ROOT.glob("*.py"))
    assert stray == [], (
        f"loose Python files at the repo root: {stray} — one-off drivers "
        "belong in scripts/experiments/ (see its README), package code in "
        "chimera/, tooling in scripts/."
    )


def test_tracked_root_entries_are_the_deliberate_set() -> None:
    entries = _tracked_root_entries()
    if entries is None:
        pytest.skip("git unavailable — root gate needs a checkout")
    unexpected = sorted(entries - ALLOWED_ROOT_ENTRIES)
    # Only ADDITIONS gate; a removed entry needs no test edit.
    assert unexpected == [], (
        f"new tracked entries at the repo root: {unexpected} — if deliberate, "
        "add them to ALLOWED_ROOT_ENTRIES in this test, in the same commit, "
        "with a reason."
    )

"""Tests for the ``--version`` flag wired by audit H-1.

Both top-level (``chimera --version``) and subcommand (``chimera mink
--version``) flag exits 0 and prints a semver-shaped string.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


_SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m chimera.cli.main`` with ``args`` and capture output."""
    return subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )


def test_mink_version_flag_exits_zero_and_prints_semver() -> None:
    """``chimera mink --version`` exits 0 and stdout contains a semver."""
    proc = _run("mink", "--version")
    assert proc.returncode == 0, proc.stderr
    # argparse's `action="version"` prints to stdout in Python 3.4+.
    combined = proc.stdout + proc.stderr
    assert "mink" in combined, combined
    assert _SEMVER_RE.search(combined), combined


def test_root_version_flag_exits_zero_and_prints_semver() -> None:
    """``chimera --version`` (top-level) also exits 0 with a semver string."""
    proc = _run("--version")
    assert proc.returncode == 0, proc.stderr
    combined = proc.stdout + proc.stderr
    assert "chimera" in combined.lower(), combined
    assert _SEMVER_RE.search(combined), combined


def test_mink_version_appears_in_help_text() -> None:
    """``chimera mink --help`` advertises ``--version`` in its options block."""
    proc = _run("mink", "--help")
    assert proc.returncode == 0
    assert "--version" in proc.stdout

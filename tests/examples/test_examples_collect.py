"""Static checks for the per-CLI quickstart example scripts.

These tests are intentionally **static-only** — they parse the example
files as Python AST and inspect file metadata, but they never invoke
the underlying ``chimera`` CLI (which would require live API keys and
produce side effects under ``~/.chimera/``).

Covered scripts:
    examples/<cli>_quickstart.py for cli in
    {mink, otter, ferret, weasel, shrew, stoat, badger}.

Why static-only:
    The point of this collector is to catch syntax regressions and
    docstring drift on every CI run. End-to-end exercise of the CLIs
    happens in the per-CLI integration suites (see
    ``tests/integration/test_mink_capabilities.py`` and friends).
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples"

# Ordered to match the wave-11 codename catalog.
CLI_CODENAMES = ("mink", "otter", "ferret", "weasel", "shrew", "stoat", "badger")


def _example_path(cli: str) -> Path:
    """Return the absolute path to ``examples/<cli>_quickstart.py``."""
    return EXAMPLES_DIR / f"{cli}_quickstart.py"


@pytest.mark.parametrize("cli", CLI_CODENAMES)
def test_each_example_parses(cli: str) -> None:
    """Each example must parse as valid Python (no SyntaxError)."""
    path = _example_path(cli)
    assert path.is_file(), f"missing example: {path}"
    source = path.read_text(encoding="utf-8")
    # Will raise SyntaxError on bad syntax; pytest surfaces it as failure.
    ast.parse(source, filename=str(path))


@pytest.mark.parametrize("cli", CLI_CODENAMES)
def test_each_example_has_docstring(cli: str) -> None:
    """Each example must have a non-empty module docstring."""
    path = _example_path(cli)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstring = ast.get_docstring(tree)
    assert docstring is not None, f"{path.name}: missing module docstring"
    assert docstring.strip(), f"{path.name}: docstring is empty"


@pytest.mark.parametrize("cli", CLI_CODENAMES)
def test_each_example_has_main(cli: str) -> None:
    """Each example must define a top-level ``def main()``."""
    path = _example_path(cli)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    has_main = any(
        isinstance(node, ast.FunctionDef) and node.name == "main"
        for node in tree.body
    )
    assert has_main, f"{path.name}: no top-level `def main()` found"


@pytest.mark.parametrize("cli", CLI_CODENAMES)
def test_each_example_executable(cli: str) -> None:
    """Each example must be either executable or carry a shebang line.

    Both conditions are valid for "this file can be invoked directly":
    POSIX execute bit (``chmod +x``) or a ``#!/usr/bin/env python3``
    leader. We accept either so the test passes on filesystems that
    don't preserve the execute bit (e.g. Windows tarball extracts).
    """
    path = _example_path(cli)
    has_shebang = path.read_text(encoding="utf-8").startswith("#!")
    is_exec = os.access(path, os.X_OK)
    assert has_shebang or is_exec, (
        f"{path.name}: neither executable bit nor shebang line present"
    )


def test_all_seven_codenames_covered() -> None:
    """Sanity check: there are exactly 7 codename quickstarts."""
    found = sorted(
        p.name for p in EXAMPLES_DIR.glob("*_quickstart.py")
        if p.stem.removesuffix("_quickstart") in CLI_CODENAMES
    )
    expected = sorted(f"{cli}_quickstart.py" for cli in CLI_CODENAMES)
    assert found == expected, f"codename quickstart drift: {found} != {expected}"

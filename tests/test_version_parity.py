"""The version is declared in two places; this pins that they cannot diverge.

``pyproject.toml`` feeds the wheel's METADATA, and ``chimera/__init__.py``
feeds ``chimera.__version__`` (what ``chimera --version`` and the statusline
print). The 0.9.2.1 release prep bumped the first and missed the second, so
the built wheel said ``Version: 0.9.2.1`` while the code inside reported
``0.9.2.1.dev0`` — caught only by the pre-release review's wheel inspection.
A release bump now fails the suite until both sites agree.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import chimera


def test_dunder_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert chimera.__version__ == declared, (
        f"chimera.__version__={chimera.__version__!r} but pyproject declares "
        f"{declared!r} — bump BOTH (the wheel's METADATA comes from pyproject, "
        f"the runtime string from chimera/__init__.py)"
    )

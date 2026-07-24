"""``glob_match`` — the one definition of what ``list_files(pattern)`` means.

:class:`~chimera.env.local.LocalEnvironment` filters with
:meth:`pathlib.Path.glob`.  Backends that enumerate paths remotely (E2B,
Daytona, …) have to reproduce those semantics in Python, and the tempting
shortcut — :func:`fnmatch.fnmatch` — is wrong, because its ``*`` crosses ``/``
and so reports nested files for ``"*.py"``.

The parity test below is the real guard: it runs each pattern through a live
``LocalEnvironment`` on a temp tree and asserts :func:`glob_match` selects
exactly the same files.  If pathlib's behaviour shifts under a new Python, this
fails rather than letting backends drift apart silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.env.base import glob_match
from chimera.env.local import LocalEnvironment

# Patterns exercised in both directions. ``**`` on its own is excluded: it
# means "directories only" before Python 3.13 and "everything" after, so it is
# not a stable cross-version contract for either side.
PATTERNS = [
    "**/*",
    "*.py",
    "*",
    "**/*.py",
    "sub/*",
    "sub/*.py",
    "sub/**/*.py",
    "**/deep/*",
    "?.py",
    "[ab].py",
    "[!a]*.py",
    "a.py",
    "nomatch/*",
]

TREE = [
    "a.py",
    "b.py",
    "readme.md",
    "sub/c.py",
    "sub/notes.txt",
    "sub/deep/d.py",
    "sub/deep/e.md",
]


@pytest.fixture()
def local_env(tmp_path: Path) -> LocalEnvironment:
    env = LocalEnvironment(workdir=str(tmp_path))
    env.setup()
    for rel in TREE:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    return env


@pytest.mark.parametrize("pattern", PATTERNS)
def test_matches_local_environment_exactly(
    local_env: LocalEnvironment, pattern: str
) -> None:
    """glob_match must select what pathlib selects — for every backend."""
    expected = sorted(local_env.list_files(pattern))
    actual = sorted(p for p in TREE if glob_match(p, pattern))
    assert actual == expected, f"divergence on {pattern!r}"


def test_single_star_stops_at_the_separator() -> None:
    """The bug this helper exists to prevent."""
    assert glob_match("a.py", "*.py")
    assert not glob_match("sub/c.py", "*.py")


def test_double_star_spans_segments_and_may_match_none() -> None:
    assert glob_match("a.py", "**/*.py")
    assert glob_match("sub/deep/d.py", "**/*.py")
    assert glob_match("sub/c.py", "sub/**/*.py")


def test_question_mark_is_a_single_non_separator_char() -> None:
    assert glob_match("a.py", "?.py")
    assert not glob_match("ab.py", "?.py")
    assert not glob_match("s/y", "??y")


def test_character_classes_and_negation() -> None:
    assert glob_match("a.py", "[ab].py")
    assert not glob_match("c.py", "[ab].py")
    assert glob_match("c.py", "[!ab].py")


def test_pattern_is_anchored_at_both_ends() -> None:
    assert not glob_match("xa.py", "a.py")
    assert not glob_match("a.pyc", "a.py")


def test_unterminated_character_class_is_a_literal() -> None:
    assert glob_match("[a.py", "[a.py")


def test_repeated_patterns_are_compiled_once() -> None:
    """list_files() calls this per candidate path — recompiling would be O(n)."""
    from chimera.env.base import _compiled

    glob_match("a.py", "cache-probe-*.py")
    hits_before = _compiled.cache_info().hits
    for _ in range(5):
        glob_match("b.py", "cache-probe-*.py")
    assert _compiled.cache_info().hits == hits_before + 5

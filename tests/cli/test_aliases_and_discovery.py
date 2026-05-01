"""Tests for wave-11 A3 + A5: purpose aliases + ``chimera agents`` discovery.

Covers:
* Each of the 7 codenames has a working purpose alias that routes
  identically to the codename (verified via ``--version`` parity).
* ``chimera agents`` lists all 7 CLIs in both text and JSON formats.
* The catalogue's alias and inspiration columns match the documented map.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from chimera.cli import agents_discovery


# ---------------------------------------------------------------------------
# Catalogue shape
# ---------------------------------------------------------------------------


_EXPECTED_PAIRS = [
    ("mink", "tui", "Claude Code"),
    ("otter", "multi", "opencode"),
    ("ferret", "sandbox", "codex"),
    ("weasel", "mini", "pi"),
    ("shrew", "tiny", "little-coder"),
    ("stoat", "shell", "kimi-cli"),
    ("badger", "strict", "claw-code"),
]


def test_catalogue_has_seven_entries() -> None:
    """All 7 codenames are listed."""
    assert len(agents_discovery._CATALOGUE) == 7


def test_catalogue_codenames_in_canonical_order() -> None:
    """Order: mink, otter, ferret, weasel, shrew, stoat, badger."""
    expected = [pair[0] for pair in _EXPECTED_PAIRS]
    actual = [e.codename for e in agents_discovery._CATALOGUE]
    assert actual == expected


@pytest.mark.parametrize("codename,alias,inspired_substr", _EXPECTED_PAIRS)
def test_catalogue_entry_shape(codename: str, alias: str, inspired_substr: str) -> None:
    """Each catalogue entry has the documented codename / alias / inspiration."""
    entry = next(e for e in agents_discovery._CATALOGUE if e.codename == codename)
    assert entry.alias == alias
    assert inspired_substr.lower() in entry.inspired_by.lower()
    assert entry.pitch  # non-empty


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


def test_text_format_includes_all_codenames() -> None:
    text = agents_discovery._format_text()
    for codename, alias, _inspired in _EXPECTED_PAIRS:
        assert codename in text
        assert alias in text


def test_json_format_round_trips() -> None:
    payload = json.loads(agents_discovery._format_json())
    assert isinstance(payload, list)
    assert len(payload) == 7
    payload_codenames = [item["codename"] for item in payload]
    assert payload_codenames == [pair[0] for pair in _EXPECTED_PAIRS]
    for item in payload:
        assert "alias" in item
        assert "inspired_by" in item
        assert "pitch" in item


# ---------------------------------------------------------------------------
# Live alias parity (subprocess: -m chimera <alias> --version)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "codename,alias",
    [
        ("mink", "tui"),
        ("otter", "multi"),
        ("ferret", "sandbox"),
        ("weasel", "mini"),
        ("shrew", "tiny"),
        ("stoat", "shell"),
        ("badger", "strict"),
    ],
)
def test_alias_version_parity(codename: str, alias: str) -> None:
    """`chimera <alias> --version` ≡ `chimera <codename> --version` output."""
    cmd_codename = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", codename, "--version"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    cmd_alias = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", alias, "--version"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    # Both commands print the version string then exit 0.
    assert cmd_codename.returncode == 0, cmd_codename.stderr
    assert cmd_alias.returncode == 0, cmd_alias.stderr
    # The output should be identical (both name the codename, since
    # --version uses the canonical name regardless of which alias was
    # passed in).
    assert cmd_codename.stdout == cmd_alias.stdout, (
        f"alias '{alias}' diverges from codename '{codename}'\n"
        f"codename stdout: {cmd_codename.stdout!r}\n"
        f"alias stdout:    {cmd_alias.stdout!r}"
    )

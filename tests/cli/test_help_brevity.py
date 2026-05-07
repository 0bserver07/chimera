"""A10-W11: ``--help`` <=50 lines + ``--help-long`` smoke tests.

Each per-CLI ``add_arguments`` registers its flags into argument groups
and exposes a ``--help-long`` flag. Standard ``--help`` output must stay
under 50 lines; ``--help-long`` must succeed (rc=0) and emit the
``### Detailed flag descriptions`` marker.

Tests invoke each CLI via the same ``argparse`` parser :mod:`chimera.cli.main`
builds, sidestepping a subprocess hop so failures don't depend on
``uv run`` being on ``$PATH``. Where a CLI lazy-loads on import, we tolerate
``ImportError`` and skip with a clear reason.
"""
from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout
from typing import Iterable

import pytest


# WHY: the canonical animal CLIs registered as wave-9/10 subcommands. Order
# matches the orchestrator's wave-11 handoff doc so failures point at one
# CLI cleanly. Each entry is the subcommand name as ``chimera <name>``.
_CLIS: tuple[str, ...] = (
    "mink",
    "otter",
    "ferret",
    "weasel",
    "shrew",
    "stoat",
    "badger",
)


def _build_subparser(cli: str) -> argparse.ArgumentParser:
    """Construct a fresh subparser for ``cli`` and run its ``add_arguments``.

    We rebuild the parser per test rather than re-using the module-level
    ``main.build_parser`` to avoid action-name collisions when several
    tests parameterize across the same module-state-aware ``add_arguments``
    (``_PARSER`` is overwritten by each call, which is what we want).
    """
    if cli == "mink":
        from chimera.mink import cli as mod
    elif cli == "otter":
        from chimera.otter import cli as mod
    elif cli == "ferret":
        from chimera.ferret import cli as mod
    elif cli == "weasel":
        from chimera.weasel import cli as mod
    elif cli == "shrew":
        from chimera.shrew import cli as mod
    elif cli == "stoat":
        from chimera.stoat import cli as mod
    elif cli == "badger":
        from chimera.badger import cli as mod
    else:  # pragma: no cover — guard against typos in _CLIS
        raise AssertionError(f"unknown cli: {cli}")

    parser = argparse.ArgumentParser(prog=f"chimera {cli}")
    mod.add_arguments(parser)
    return parser


def _format_help_lines(parser: argparse.ArgumentParser) -> list[str]:
    """Return ``parser.format_help()`` split into individual lines."""
    return parser.format_help().splitlines()


@pytest.mark.parametrize("cli", _CLIS)
def test_help_under_50_lines(cli: str) -> None:
    """Standard ``--help`` output for *cli* fits in <=50 lines.

    Wave-11 task A10's headline contract: the default help screen must
    stay scannable. Long-form flag descriptions move to ``--help-long``.
    """
    parser = _build_subparser(cli)
    lines = _format_help_lines(parser)
    assert len(lines) <= 50, (
        f"chimera {cli} --help produced {len(lines)} lines (>50). "
        f"Move detail to _LONG_HELP and tighten short helps."
    )


def _run_with_help_long(cli: str) -> tuple[int, str]:
    """Invoke the CLI's ``run`` with ``args.help_long=True`` and capture stdout.

    Returns:
        ``(rc, stdout)``. ``rc`` is the int returned by ``run``; ``stdout``
        is everything written to ``sys.stdout`` during the call.
    """
    if cli == "mink":
        from chimera.mink import cli as mod
    elif cli == "otter":
        from chimera.otter import cli as mod
    elif cli == "ferret":
        from chimera.ferret import cli as mod
    elif cli == "weasel":
        from chimera.weasel import cli as mod
    elif cli == "shrew":
        from chimera.shrew import cli as mod
    elif cli == "stoat":
        from chimera.stoat import cli as mod
    elif cli == "badger":
        from chimera.badger import cli as mod
    else:  # pragma: no cover
        raise AssertionError(f"unknown cli: {cli}")

    # Build a parser so the module-level ``_PARSER`` is populated, then
    # synthesise a parsed namespace with ``help_long=True``. We pass an
    # empty arglist so positional defaults stick (None / [] across CLIs).
    parser = _build_subparser(cli)
    args = parser.parse_args([])
    args.help_long = True

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.run(args)
    return rc, buf.getvalue()


@pytest.mark.parametrize("cli", _CLIS)
def test_help_long_works(cli: str) -> None:
    """``--help-long`` exits 0 and emits the ``Detailed flag descriptions`` marker.

    The marker is the contract :func:`chimera.cli.help_long.print_help_long`
    enforces; consumers (docs, tests, IDE plugins) parse around it.
    """
    rc, stdout = _run_with_help_long(cli)
    assert rc == 0, f"chimera {cli} --help-long returned rc={rc}"
    assert "Detailed flag descriptions" in stdout, (
        f"chimera {cli} --help-long output missing the marker. "
        f"First 200 chars: {stdout[:200]!r}"
    )


def test_long_help_lists_every_cli() -> None:
    """Sanity check: ``_CLIS`` matches the seven animal-named coding agents.

    Stops bit-rot if a future wave adds an eighth CLI: the test-author
    sees the count mismatch immediately.
    """
    assert len(_CLIS) == 7, "expected exactly 7 animal CLIs in wave-11"
    expected: Iterable[str] = (
        "mink", "otter", "ferret", "weasel", "shrew", "stoat", "badger",
    )
    assert set(_CLIS) == set(expected)

"""Shared ``--help-long`` helper for the per-CLI modules.

Wave-11 task A10 introduced a ``--help-long`` flag on each animal-named
CLI (``mink`` / ``otter`` / ``ferret`` / ``weasel`` / ``shrew`` / ``stoat`` /
``badger``). When set, the CLI prints the standard ``--help`` output
followed by a *Detailed flag descriptions* section sourced from a
per-module ``_LONG_HELP`` dict.

The standard ``--help`` is constrained to <=50 lines (we group flags into
``Core`` / ``Behavior`` / ``Output`` / ``Persistence`` argument groups and
keep ``help=`` strings tight); ``--help-long`` is the escape hatch users
reach for when they need full context.

This module exposes one helper, :func:`print_help_long`, which each CLI's
``run()`` calls when ``args.help_long`` is True.
"""

from __future__ import annotations

import argparse
import sys
from typing import Mapping


def print_help_long(
    parser: argparse.ArgumentParser | None,
    long_help: Mapping[str, str],
) -> None:
    """Emit ``parser.format_help()`` plus the detailed flag descriptions.

    The output marker ``"### Detailed flag descriptions"`` is asserted by
    ``tests/cli/test_help_brevity.py``; do not rename it without updating
    the tests.

    Args:
        parser: The argparse parser the calling CLI registered its flags
            on. ``None`` is tolerated for unit-test scenarios where
            ``add_arguments`` was never invoked — in that case only the
            long-form descriptions are printed.
        long_help: Mapping of flag spelling (e.g. ``"--model"``) to the
            verbose description body. Iteration order is preserved.
    """
    if parser is not None:
        sys.stdout.write(parser.format_help())
    sys.stdout.write("\n### Detailed flag descriptions\n\n")
    for flag, body in long_help.items():
        sys.stdout.write(f"{flag}:\n  {body}\n\n")

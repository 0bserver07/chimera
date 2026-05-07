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

This module exposes two helpers:

* :func:`print_help_long` — used by each CLI's ``run()`` when
  ``args.help_long`` is True.
* :func:`register_argument` — preferred helper for adding new flags. It
  wraps :py:meth:`argparse.ArgumentParser.add_argument` and auto-promotes
  any verbose description into the per-CLI ``_LONG_HELP`` dict, so a
  flag's full help can never accidentally bloat ``chimera <cli> --help``
  past the 50-line ceiling.
"""

from __future__ import annotations

import argparse
from typing import Any, Mapping, MutableMapping

import sys

# Threshold above which a ``help=`` string is treated as "too long for
# the short help screen" and auto-promoted to ``_LONG_HELP``. Tuned so a
# typical wrapped-once help line (≤60 chars) stays in the short surface,
# while a help line that would consume more than one wrapped row is
# pushed to the long surface. Keep this in sync with the 50-line
# ceiling enforced by ``tests/cli/test_help_brevity.py``.
SHORT_HELP_MAX: int = 60


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


def _flag_key(flag_args: tuple[str, ...]) -> str:
    """Pick the canonical key for a flag in ``_LONG_HELP``.

    Prefers the long ``--option`` form when present, falls back to the
    short flag, and finally to the positional name. The convention
    matches what each CLI already keys its ``_LONG_HELP`` dict on.

    Args:
        flag_args: The positional ``*args`` passed to ``add_argument`` —
            e.g. ``("-c", "--continue")`` or ``("--model",)`` or
            ``("PROMPT",)``.

    Returns:
        The chosen key. For the ``-c / --continue`` pair we use the
        ``"-c / --continue"`` joined form to match the existing mink
        convention.
    """
    longs = [a for a in flag_args if a.startswith("--")]
    shorts = [a for a in flag_args if a.startswith("-") and not a.startswith("--")]
    if longs and shorts:
        return f"{shorts[0]} / {longs[0]}"
    if longs:
        return longs[0]
    if shorts:
        return shorts[0]
    if flag_args:
        return flag_args[0]
    raise ValueError("register_argument requires at least one flag/positional name")


def register_argument(
    parser: argparse.ArgumentParser | argparse._ArgumentGroup,
    *flag_args: str,
    help_short: str | None = None,
    help_long: str | None = None,
    long_help: MutableMapping[str, str] | None = None,
    help: str | None = None,
    **kwargs: Any,
) -> argparse.Action:
    """Wrap :py:meth:`add_argument` with auto-promotion of verbose help.

    The default flow is:

    1. If ``help_long`` is given, register it in ``long_help`` (when a
       dict is supplied) under the canonical flag key.
    2. The argparse-visible help string is ``help_short`` if explicitly
       provided, otherwise ``help_long`` truncated to fit
       ``SHORT_HELP_MAX``, otherwise ``help`` itself.
    3. If only a single ``help`` is provided AND it exceeds
       ``SHORT_HELP_MAX``, the function auto-promotes: the full string
       lands in ``long_help``, and a truncated form (split on first
       sentence boundary, falling back to a hard slice + ``" …"``) goes
       to argparse. The auto-promotion is the safety net the task spec
       calls out — a future contributor cannot accidentally bloat
       ``chimera <cli> --help`` simply by writing a long ``help=``.
    4. Any additional ``kwargs`` are forwarded verbatim to argparse.

    Args:
        parser: An :class:`argparse.ArgumentParser` or argument group.
        *flag_args: Positional argument names passed through to
            ``add_argument`` — e.g. ``("--model",)`` or
            ``("-p", "--print")`` or ``("PROMPT",)``.
        help_short: Optional explicit short help. Wins over auto-truncation.
        help_long: Optional verbose help. Registered in ``long_help``.
        long_help: Optional ``_LONG_HELP`` dict to mutate. When ``None``,
            the long form is silently dropped — callers who want
            auto-promotion to the long surface must pass the dict.
        help: Standard argparse ``help=`` kwarg. When >SHORT_HELP_MAX
            chars and neither ``help_short`` nor ``help_long`` is given,
            the helper auto-promotes it.
        **kwargs: Forwarded to :py:meth:`add_argument`.

    Returns:
        The :class:`argparse.Action` returned by ``add_argument``.

    Raises:
        ValueError: If no flag/positional names are provided.
    """
    key = _flag_key(flag_args)

    chosen_short: str | None
    chosen_long: str | None = help_long

    if help_short is not None:
        chosen_short = help_short
    elif help_long is not None:
        chosen_short = _truncate(help_long)
    elif help is not None and len(help) > SHORT_HELP_MAX:
        # Auto-promote: long form goes to long_help, short form is
        # truncated for argparse.
        chosen_long = help
        chosen_short = _truncate(help)
    else:
        chosen_short = help

    if chosen_long and long_help is not None:
        long_help[key] = chosen_long

    final_kwargs = dict(kwargs)
    if chosen_short is not None:
        final_kwargs["help"] = chosen_short
    return parser.add_argument(*flag_args, **final_kwargs)


def _truncate(text: str, *, max_chars: int = SHORT_HELP_MAX) -> str:
    """Truncate ``text`` to a short help line.

    Tries to cut at the first sentence boundary (``". "``) so the short
    form reads naturally. Falls back to a hard slice with a trailing
    ``" …"`` when no boundary is found in range. Never returns more than
    ``max_chars`` + 2 characters (the ``" …"`` suffix is two chars).

    Args:
        text: Source string to truncate.
        max_chars: Cap for the result before any suffix.

    Returns:
        A trimmed short-help string. Strings already <= ``max_chars`` are
        returned unchanged.
    """
    if len(text) <= max_chars:
        return text
    # Try to cut at a sentence boundary inside the budget.
    head = text[:max_chars]
    cut = head.rfind(". ")
    if cut >= 20:  # Ignore implausibly short sentence fragments.
        return text[: cut + 1]
    # No good boundary — hard truncate with an ellipsis.
    return head.rstrip() + " …"

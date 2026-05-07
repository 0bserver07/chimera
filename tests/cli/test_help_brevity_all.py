"""W14-9 cross-CLI help-brevity assertions.

Wave-13 task E6 added :func:`chimera.cli.help_long.register_argument` so a
future contributor who pastes a verbose ``help=`` string into any of the
seven animal-named CLIs can't silently bloat ``--help`` past the 50-line
ceiling — long forms auto-promote into the per-CLI ``_LONG_HELP`` dict.

Until W14-9, only ``mink`` and ``ferret`` actually routed through the
helper. After W14-9, every animal CLI imports ``register_argument`` and
uses it for at least one flag, so the auto-promotion safety net is wired
across the whole surface.

This file pins three contracts so a regression on any CLI fails one test:

1. **Line ceiling.** ``chimera <cli> --help`` stays <=50 lines for each
   of the seven CLIs (deliberate overlap with
   :mod:`tests.cli.test_help_brevity` — keeps the failure localised when
   one CLI regresses).
2. **register_argument plumbed.** Each CLI module imports
   ``register_argument`` from :mod:`chimera.cli.help_long` AND exercises
   it at least once on its parser. Static-import + grep-style runtime
   inspection catches both the "forgot to import" and the "imported but
   never called" regressions.
3. **--help-long completeness.** Every CLI's ``_LONG_HELP`` dict is
   non-empty and ``--help-long`` renders the standard
   ``Detailed flag descriptions`` marker plus at least one flag entry
   from the dict. Otherwise the auto-promote contract degrades silently.
"""
from __future__ import annotations

import argparse
import inspect
import io
from contextlib import redirect_stdout

import pytest

# Same canonical CLI list :mod:`tests.cli.test_help_brevity` parameterises
# over. Order matches the wave-11 orchestrator's handoff doc so failures
# point at one CLI cleanly. Kept in lockstep with that file's ``_CLIS``
# tuple via the smoke-test below.
_CLIS: tuple[str, ...] = (
    "mink",
    "otter",
    "ferret",
    "weasel",
    "shrew",
    "stoat",
    "badger",
)


def _import_cli_module(cli: str):
    """Import the per-CLI ``cli`` module by its animal name."""
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
    else:  # pragma: no cover -- guard against typos in _CLIS
        raise AssertionError(f"unknown cli: {cli}")
    return mod


def _build_subparser(cli: str) -> argparse.ArgumentParser:
    """Build a fresh parser and run the CLI's ``add_arguments`` against it."""
    mod = _import_cli_module(cli)
    parser = argparse.ArgumentParser(prog=f"chimera {cli}")
    mod.add_arguments(parser)
    return parser


# ---------------------------------------------------------------------------
# 1. Line ceiling parity: --help <= 50 lines for every CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cli", _CLIS)
def test_help_under_50_lines_all_clis(cli: str) -> None:
    """``chimera <cli> --help`` stays <=50 lines after the W14-9 migration.

    Overlaps with ``test_help_brevity.py`` so a regression on a single
    CLI fails *both* tests, making it obvious in CI which animal flipped.
    """
    parser = _build_subparser(cli)
    lines = parser.format_help().splitlines()
    assert len(lines) <= 50, (
        f"chimera {cli} --help produced {len(lines)} lines (>50). "
        f"Use register_argument(... long_help=_LONG_HELP) to push detail "
        f"into --help-long instead of inflating the short surface."
    )


# ---------------------------------------------------------------------------
# 2. register_argument is plumbed in every CLI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cli", _CLIS)
def test_register_argument_imported_in_each_cli(cli: str) -> None:
    """Each CLI module imports ``register_argument`` from the shared helper.

    The simplest check that the W14-9 migration actually landed — without
    the import, no ``register_argument(...)`` call could possibly fire.
    """
    mod = _import_cli_module(cli)
    assert hasattr(mod, "register_argument"), (
        f"chimera.{cli}.cli must import register_argument from "
        f"chimera.cli.help_long to participate in the auto-promotion "
        f"safety net (W14-9)."
    )
    # Sanity: it points at the canonical helper, not a local shadow.
    from chimera.cli.help_long import register_argument as canonical
    assert mod.register_argument is canonical, (
        f"chimera.{cli}.cli.register_argument should be the helper from "
        f"chimera.cli.help_long, not a local shadow."
    )


@pytest.mark.parametrize("cli", _CLIS)
def test_register_argument_actually_called_in_add_arguments(cli: str) -> None:
    """Each CLI's ``add_arguments`` source contains ``register_argument(``.

    Source-level grep — coarser than instrumenting the parser, but
    catches the "imported but never used" regression that pyright won't
    flag as an error. A future migration can swap this for a parser-call
    counter without changing the contract.
    """
    mod = _import_cli_module(cli)
    src = inspect.getsource(mod.add_arguments)
    assert "register_argument(" in src, (
        f"chimera.{cli}.cli.add_arguments should call register_argument "
        f"on at least one flag. Without a call, the W14-9 auto-promotion "
        f"safety net never fires and a future verbose help= will silently "
        f"bloat --help."
    )


# ---------------------------------------------------------------------------
# 3. --help-long surfaces the long descriptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cli", _CLIS)
def test_long_help_dict_non_empty(cli: str) -> None:
    """Each CLI exposes a non-empty ``_LONG_HELP`` dict.

    Empty would mean every flag's verbose description was lost, which
    the migration must not regress.
    """
    mod = _import_cli_module(cli)
    long_help = getattr(mod, "_LONG_HELP", None)
    assert isinstance(long_help, dict), (
        f"chimera.{cli}.cli must export a ``_LONG_HELP: dict[str, str]``."
    )
    assert long_help, (
        f"chimera.{cli}.cli._LONG_HELP is empty after the W14-9 migration; "
        f"--help-long would have nothing to render."
    )


@pytest.mark.parametrize("cli", _CLIS)
def test_help_long_renders_marker_and_entries(cli: str) -> None:
    """``chimera <cli> --help-long`` succeeds, marker present, dict surfaced.

    Drives the CLI's ``run`` with ``args.help_long=True`` and asserts:

    * rc == 0
    * the canonical ``"### Detailed flag descriptions"`` marker is
      present (consumers grep for this).
    * at least one ``_LONG_HELP`` body string actually appears in the
      output — guards against a future change that prints the marker
      but forgets to enumerate the dict.
    """
    mod = _import_cli_module(cli)

    parser = _build_subparser(cli)
    args = parser.parse_args([])
    args.help_long = True

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.run(args)
    out = buf.getvalue()

    assert rc == 0, f"chimera {cli} --help-long returned rc={rc}"
    assert "Detailed flag descriptions" in out, (
        f"chimera {cli} --help-long output missing the marker. "
        f"First 200 chars: {out[:200]!r}"
    )

    # At least one verbose body string from _LONG_HELP must surface.
    long_help = mod._LONG_HELP
    bodies = [body for body in long_help.values() if body]
    surfaced = [body for body in bodies if body in out]
    assert surfaced, (
        f"chimera {cli} --help-long printed the marker but no "
        f"_LONG_HELP body strings — print_help_long is likely no longer "
        f"iterating the dict."
    )


# ---------------------------------------------------------------------------
# Smoke: keep _CLIS in lockstep with test_help_brevity.py
# ---------------------------------------------------------------------------


def test_clis_lockstep_with_help_brevity() -> None:
    """The canonical 7-CLI tuple matches ``tests.cli.test_help_brevity._CLIS``.

    If a future wave introduces an eighth CLI, both files need updating
    together — this assertion makes that explicit.
    """
    from tests.cli.test_help_brevity import _CLIS as canonical
    assert set(_CLIS) == set(canonical)
    assert len(_CLIS) == len(canonical) == 7

"""Tests for the auto-promotion behaviour of ``register_argument``.

Wave-13 task E6 extended :mod:`chimera.cli.help_long` with a smart
``register_argument`` wrapper that prevents future flag additions from
silently bloating ``chimera <cli> --help`` past the 50-line ceiling.

Contract:

1. A short ``help=`` string passes through unchanged.
2. A verbose ``help=`` string (>``SHORT_HELP_MAX``) is split — argparse
   sees a truncated short form, the full form is registered in the
   per-CLI ``_LONG_HELP`` dict.
3. Explicit ``help_short`` / ``help_long`` always win over auto-promotion.
4. Truncation cuts at a sentence boundary when one fits in the budget;
   otherwise it hard-truncates with a ``" …"`` suffix.
5. The chosen ``_LONG_HELP`` key matches the flag form the existing
   per-CLI dicts use (``"--option"`` for long flags, ``"-c / --continue"``
   for paired short/long, positional name for positionals).
"""
from __future__ import annotations

import argparse

import pytest

from chimera.cli.help_long import (
    SHORT_HELP_MAX,
    _flag_key,
    _truncate,
    print_help_long,
    register_argument,
)


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


def test_truncate_short_string_unchanged() -> None:
    """Short strings are returned verbatim."""
    text = "Working directory."
    assert _truncate(text) == text


def test_truncate_cuts_on_sentence_boundary() -> None:
    """A long help with a sentence boundary in budget cuts cleanly."""
    text = (
        "Per-tool-call timeout. When exceeded, the tool returns an "
        "error result rather than crashing the run."
    )
    out = _truncate(text)
    assert out.endswith(".")
    assert "When exceeded" not in out
    assert len(out) <= SHORT_HELP_MAX + 2


def test_truncate_falls_back_to_hard_slice() -> None:
    """When no sentence boundary fits, hard-truncate with a hint."""
    text = "x" * 200
    out = _truncate(text)
    assert out.endswith(" …")
    assert len(out) <= SHORT_HELP_MAX + 2


# ---------------------------------------------------------------------------
# _flag_key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args,expected",
    [
        (("--model",), "--model"),
        (("-c", "--continue"), "-c / --continue"),
        (("--help-long",), "--help-long"),
        (("PROMPT",), "PROMPT"),
        (("-p", "--print"), "-p / --print"),
    ],
)
def test_flag_key(args: tuple[str, ...], expected: str) -> None:
    assert _flag_key(args) == expected


def test_flag_key_empty_raises() -> None:
    with pytest.raises(ValueError):
        _flag_key(())


# ---------------------------------------------------------------------------
# register_argument — auto-promotion
# ---------------------------------------------------------------------------


def _fresh_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog="test")


def test_short_help_passes_through() -> None:
    """``help=`` strings within budget are not promoted."""
    parser = _fresh_parser()
    long_help: dict[str, str] = {}
    register_argument(
        parser,
        "--cwd",
        long_help=long_help,
        help="Working directory (default: cwd).",
    )
    fmt = parser.format_help()
    assert "Working directory (default: cwd)." in fmt
    assert "--cwd" not in long_help, (
        "short help strings should not be auto-promoted"
    )


def test_long_help_auto_promoted() -> None:
    """A verbose ``help=`` string lands in long_help, argparse sees the short form."""
    parser = _fresh_parser()
    long_help: dict[str, str] = {}
    full = (
        "Approval surface controlling tool execution. Legacy values: "
        "default, acceptEdits, bypassPermissions, plan. 5-mode standard "
        "(w13): read-only, suggest, auto, yolo, strict. See docs."
    )
    assert len(full) > SHORT_HELP_MAX, "test fixture invariant"
    register_argument(
        parser,
        "--permission-mode",
        long_help=long_help,
        help=full,
    )
    fmt = parser.format_help()
    # Argparse short form: just the truncated lead.
    assert "5-mode standard" not in fmt, (
        "second-sentence detail should NOT appear in short help"
    )
    assert "Approval surface" in fmt, (
        "lead sentence should still appear in short help"
    )
    # Long form: full string registered under the canonical key.
    assert long_help["--permission-mode"] == full


def test_explicit_short_and_long_both_honored() -> None:
    """Explicit ``help_short`` + ``help_long`` skip auto-promotion entirely."""
    parser = _fresh_parser()
    long_help: dict[str, str] = {}
    register_argument(
        parser,
        "--sandbox",
        help_short="MODE (read-only|suggest|auto|yolo|strict).",
        help_long="Full sandbox mode rationale and matrix...",
        long_help=long_help,
    )
    fmt = parser.format_help()
    assert "(read-only|suggest|auto|yolo|strict)" in fmt
    assert "Full sandbox mode rationale" not in fmt
    assert long_help["--sandbox"] == "Full sandbox mode rationale and matrix..."


def test_help_long_alone_auto_truncates_short() -> None:
    """Providing only ``help_long`` derives a short form for argparse."""
    parser = _fresh_parser()
    long_help: dict[str, str] = {}
    full = (
        "Sandbox backend. Local runs the sandbox in-process. Modal "
        "delegates execution to a managed sandbox provider over the "
        "network."
    )
    register_argument(
        parser,
        "--sandbox-backend",
        help_long=full,
        long_help=long_help,
    )
    fmt = parser.format_help()
    assert "Modal" not in fmt
    assert long_help["--sandbox-backend"] == full


def test_no_long_help_dict_drops_long_form_silently() -> None:
    """When ``long_help`` is None the long form is silently discarded."""
    parser = _fresh_parser()
    full = (
        "Approval surface. Legacy: default | acceptEdits | "
        "bypassPermissions | plan. 5-mode: read-only | suggest | "
        "auto | yolo | strict."
    )
    # Should not raise even without a long_help dict.
    register_argument(
        parser,
        "--permission-mode",
        help=full,
    )
    fmt = parser.format_help()
    assert "Approval surface" in fmt


def test_register_argument_preserves_metavar_and_kwargs() -> None:
    """Forwarded kwargs (``metavar``, ``choices``, ``default``) reach argparse."""
    parser = _fresh_parser()
    long_help: dict[str, str] = {}
    register_argument(
        parser,
        "--output-format",
        long_help=long_help,
        choices=["text", "json", "stream-json"],
        default="text",
        metavar="FMT",
        help="Output format selector.",
    )
    fmt = parser.format_help()
    assert "FMT" in fmt
    # argparse renders {a,b,c} when choices+metavar both set in some
    # python versions and only the metavar in others; assert at least
    # the metavar is visible.
    assert "--output-format" in fmt


def test_register_argument_works_on_argument_groups() -> None:
    """Argument groups go through the same code path."""
    parser = _fresh_parser()
    group = parser.add_argument_group("Behavior")
    long_help: dict[str, str] = {}
    register_argument(
        group,
        "--max-steps",
        type=int,
        long_help=long_help,
        help="Max agent steps per turn (default: 50).",
        default=50,
    )
    fmt = parser.format_help()
    assert "Behavior" in fmt
    assert "--max-steps" in fmt


def test_paired_short_long_flag_uses_combined_key() -> None:
    """``-c / --continue`` form is the existing per-CLI convention."""
    parser = _fresh_parser()
    long_help: dict[str, str] = {}
    register_argument(
        parser,
        "-c",
        "--continue",
        action="store_true",
        long_help=long_help,
        help_long=(
            "Resume the most-recent run under the current working "
            "directory. Equivalent to --resume <newest-id-in-cwd>."
        ),
        help_short="Resume newest run in cwd.",
    )
    assert "-c / --continue" in long_help


# ---------------------------------------------------------------------------
# Integration: print_help_long still works with the new registration path
# ---------------------------------------------------------------------------


def test_print_help_long_includes_auto_promoted_entries(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Running ``--help-long`` includes the auto-promoted long form."""
    parser = _fresh_parser()
    long_help: dict[str, str] = {}
    full = (
        "Approval surface. Legacy: default | acceptEdits | "
        "bypassPermissions | plan. 5-mode: read-only | suggest | "
        "auto | yolo | strict. Default: read-only."
    )
    register_argument(
        parser,
        "--permission-mode",
        long_help=long_help,
        help=full,
    )
    print_help_long(parser, long_help)
    out = capsys.readouterr().out
    assert "Detailed flag descriptions" in out
    assert full in out, (
        "the verbose form must reach --help-long output verbatim"
    )

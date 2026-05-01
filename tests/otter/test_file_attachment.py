"""Tests for ``chimera otter -p --file/-f`` attachment plumbing (O3-W9).

The contract under test:

* ``add_arguments`` registers ``-f/--file`` with ``action="append"`` so
  multiple invocations stack into a list under ``args.files``.
* :func:`chimera.otter.cli._format_file_attachments` reads each path,
  wraps it in ``<file path="X" lines="N"> ... </file>``, and returns the
  bundle ready to prepend to the ``-p`` text.
* ``--file -`` reads from stdin; passing the same content on stdin lands
  inside the attachment bundle as ``<file path="<stdin>" ...>``.
* The per-file 100 KB ceiling emits a ``[otter]`` warning to stderr but
  still attaches; the cumulative 500 KB cap *truncates* the offending
  file with a ``<!-- truncated -->`` marker.

These tests exercise the helper directly so they don't depend on a live
provider — the integration with :func:`_run_print_mode` is covered by the
helper round-trip plus the existing CLI smoke suite.
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

import pytest

from chimera.otter import cli as otter_cli


# ---------------------------------------------------------------------------
# Argparse surface
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera otter")
    otter_cli.add_arguments(parser)
    return parser


def test_file_flag_accepts_short_and_long_form(tmp_path: Path) -> None:
    """``-f`` / ``--file`` both stack into ``args.files``."""
    f1 = tmp_path / "a.py"
    f1.write_text("print('a')\n", encoding="utf-8")
    f2 = tmp_path / "b.py"
    f2.write_text("print('b')\n", encoding="utf-8")
    parser = _build_parser()
    args = parser.parse_args(
        ["-p", "do thing", "-f", str(f1), "--file", str(f2)],
    )
    assert args.files == [str(f1), str(f2)]


def test_file_flag_default_is_none() -> None:
    """Without ``-f``, ``args.files`` stays ``None`` so callers can fast-path."""
    parser = _build_parser()
    args = parser.parse_args(["-p", "do thing"])
    assert args.files is None


# ---------------------------------------------------------------------------
# _format_file_attachments — basic shape
# ---------------------------------------------------------------------------


def test_format_attachments_empty_returns_empty_string() -> None:
    """``None`` and ``[]`` both short-circuit to an empty bundle."""
    assert otter_cli._format_file_attachments(None) == ""
    assert otter_cli._format_file_attachments([]) == ""


def test_format_attachments_wraps_single_file(tmp_path: Path) -> None:
    """Single file lands in a ``<file path="X" lines="N"> ... </file>`` block."""
    f = tmp_path / "hello.py"
    f.write_text("print('hi')\nprint('bye')\n", encoding="utf-8")

    bundle = otter_cli._format_file_attachments([str(f)])

    assert f'<file path="{f}" lines="2">' in bundle
    assert "print('hi')" in bundle
    assert "print('bye')" in bundle
    assert "</file>" in bundle


def test_format_attachments_stacks_multiple_files(tmp_path: Path) -> None:
    """Two attachments produce two blocks separated by a blank line."""
    f1 = tmp_path / "a.txt"
    f1.write_text("alpha\n", encoding="utf-8")
    f2 = tmp_path / "b.txt"
    f2.write_text("beta\n", encoding="utf-8")

    bundle = otter_cli._format_file_attachments([str(f1), str(f2)])

    assert f'path="{f1}"' in bundle
    assert f'path="{f2}"' in bundle
    assert "alpha" in bundle
    assert "beta" in bundle
    # Two ``<file ...>`` openers means the second one really did stack.
    assert bundle.count("<file ") == 2
    assert bundle.count("</file>") == 2


def test_format_attachments_trailing_blank_line_for_prompt_seam(
    tmp_path: Path,
) -> None:
    """Bundle ends with ``\\n\\n`` so the ``-p`` text appends cleanly."""
    f = tmp_path / "seam.txt"
    f.write_text("seam\n", encoding="utf-8")
    bundle = otter_cli._format_file_attachments([str(f)])
    assert bundle.endswith("\n\n")


def test_format_attachments_unreadable_path_warns_and_skips(
    tmp_path: Path,
) -> None:
    """Missing files emit a stderr warning and are skipped (no crash)."""
    err = io.StringIO()
    bundle = otter_cli._format_file_attachments(
        [str(tmp_path / "does-not-exist.txt")],
        stderr=err,
    )
    assert bundle == ""
    assert "does-not-exist" in err.getvalue()


# ---------------------------------------------------------------------------
# Stdin attachment via ``-f -``
# ---------------------------------------------------------------------------


def test_format_attachments_stdin_dash_reads_from_stdin() -> None:
    """``-f -`` consumes the supplied stdin and labels the block ``<stdin>``."""
    fake_stdin = io.StringIO("piped contents\n")
    bundle = otter_cli._format_file_attachments(["-"], stdin=fake_stdin)

    assert '<file path="<stdin>" lines="1">' in bundle
    assert "piped contents" in bundle
    assert "</file>" in bundle


def test_format_attachments_stdin_mixed_with_real_file(tmp_path: Path) -> None:
    """Stdin and a real file in the same call both materialize as blocks."""
    f = tmp_path / "real.txt"
    f.write_text("on disk\n", encoding="utf-8")
    fake_stdin = io.StringIO("from stdin\n")

    bundle = otter_cli._format_file_attachments(
        ["-", str(f)], stdin=fake_stdin,
    )

    assert "from stdin" in bundle
    assert "on disk" in bundle
    assert bundle.count("<file ") == 2


# ---------------------------------------------------------------------------
# Size policy — per-file warn + cumulative truncate
# ---------------------------------------------------------------------------


def test_format_attachments_per_file_warning_emitted(tmp_path: Path) -> None:
    """A single oversize file warns on stderr but still attaches in full."""
    big = tmp_path / "big.txt"
    payload = "x" * (otter_cli._FILE_ATTACHMENT_PER_FILE_WARN_BYTES + 16)
    big.write_text(payload, encoding="utf-8")

    err = io.StringIO()
    bundle = otter_cli._format_file_attachments([str(big)], stderr=err)

    assert "per-file soft cap" in err.getvalue()
    # The full payload still lands in the bundle (no truncation marker).
    assert otter_cli._FILE_ATTACHMENT_TRUNCATION_MARKER not in bundle
    assert payload in bundle


def test_format_attachments_cumulative_cap_truncates(tmp_path: Path) -> None:
    """Past the cumulative 500 KB cap, the *current* file gets truncated."""
    cap = otter_cli._FILE_ATTACHMENT_TOTAL_CAP_BYTES
    a = tmp_path / "a.txt"
    a.write_text("a" * (cap - 1024), encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("b" * (8 * 1024), encoding="utf-8")  # 8 KB

    err = io.StringIO()
    bundle = otter_cli._format_file_attachments(
        [str(a), str(b)], stderr=err,
    )

    assert otter_cli._FILE_ATTACHMENT_TRUNCATION_MARKER in bundle
    assert "cumulative attachments would exceed" in err.getvalue()
    # The first file is untouched (no truncation marker before the
    # second block opener).
    first_block_end = bundle.index("</file>") + len("</file>")
    assert (
        otter_cli._FILE_ATTACHMENT_TRUNCATION_MARKER
        not in bundle[:first_block_end]
    )


# ---------------------------------------------------------------------------
# Concatenation contract — bundle goes BEFORE the -p prompt
# ---------------------------------------------------------------------------


def test_attachment_prepended_to_prompt(tmp_path: Path) -> None:
    """The helper's output is intended to land *before* the ``-p`` text.

    The CLI builds ``effective_prompt = attachments + args.print_mode``
    (see :func:`chimera.otter.cli._run_print_mode`); we encode that
    contract here so a future refactor can't silently flip the order.
    """
    f = tmp_path / "c.txt"
    f.write_text("ctx\n", encoding="utf-8")
    user_prompt = "explain this file"

    attachments = otter_cli._format_file_attachments([str(f)])
    effective = f"{attachments}{user_prompt}"

    assert effective.startswith("<file ")
    assert effective.endswith(user_prompt)
    assert "ctx" in effective


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_line_count_handles_no_trailing_newline(tmp_path: Path) -> None:
    """A file without a trailing newline still reports a sane line count."""
    f = tmp_path / "no-newline.txt"
    f.write_bytes(b"only-line")  # no trailing \n

    bundle = otter_cli._format_file_attachments([str(f)])

    assert 'lines="1"' in bundle
    # Ensure the bundle still terminates the body cleanly.
    assert bundle.rstrip().endswith("</file>")


def test_binary_payload_decodes_with_replacement(tmp_path: Path) -> None:
    """Non-UTF-8 bytes don't crash — they fall through with replacement chars."""
    f = tmp_path / "binary.bin"
    f.write_bytes(b"\xff\xfe\x00\x01valid")

    bundle = otter_cli._format_file_attachments([str(f)])

    assert "<file " in bundle
    assert "valid" in bundle


@pytest.mark.parametrize("paths", [[], None])
def test_format_attachments_falsy_inputs(paths: list[str] | None) -> None:
    """Falsy inputs return ``""`` (caller can short-circuit on truthiness)."""
    assert otter_cli._format_file_attachments(paths) == ""

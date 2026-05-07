"""Tests for bracketed-paste support in the stoat REPL (W14-3, item 4).

Covers:

* :data:`BRACKETED_PASTE_BEGIN` / :data:`BRACKETED_PASTE_END` constants
  match xterm's escape sequences so the REPL can detect the markers.
* :func:`coalesce_bracketed_paste` returns input unchanged when no
  paste markers are present.
* :func:`coalesce_bracketed_paste` collapses a multi-line paste
  (begin marker on one line, body across several lines, end marker on
  the last line) into a single newline-joined string.
* The fast path: begin and end markers on the same line.
* Pre/post text around the markers is preserved.
* :class:`StoatRepl` ``_read_input`` calls the coalescer when
  bracketed-paste mode is on; passes through when off.
* Default constructor behavior turns bracketed-paste mode on.
* End-to-end through the REPL loop: a multi-line paste in shell mode
  runs as one bash command, not one per line.
"""
from __future__ import annotations

import io

import pytest

from chimera.stoat import repl as stoat_repl
from chimera.stoat.repl import (
    BRACKETED_PASTE_BEGIN,
    BRACKETED_PASTE_END,
    StoatRepl,
    coalesce_bracketed_paste,
)
from chimera.stoat.shell_mode import MODE_SHELL


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_paste_begin_marker_matches_xterm() -> None:
    """``BRACKETED_PASTE_BEGIN`` is xterm's documented opening sequence."""
    assert BRACKETED_PASTE_BEGIN == "\x1b[200~"


def test_paste_end_marker_matches_xterm() -> None:
    """``BRACKETED_PASTE_END`` is xterm's documented closing sequence."""
    assert BRACKETED_PASTE_END == "\x1b[201~"


# ---------------------------------------------------------------------------
# coalesce_bracketed_paste
# ---------------------------------------------------------------------------


def test_coalesce_no_marker_returns_unchanged() -> None:
    """Non-paste input is returned verbatim with no extra reads."""

    calls = {"n": 0}

    def _read_more() -> str:
        calls["n"] += 1
        return ""

    out = coalesce_bracketed_paste("ls -la", read_more=_read_more)
    assert out == "ls -la"
    assert calls["n"] == 0


def test_coalesce_single_line_paste() -> None:
    """Begin + end on the same line -> body extracted, markers stripped."""
    line = f"{BRACKETED_PASTE_BEGIN}echo one{BRACKETED_PASTE_END}"
    out = coalesce_bracketed_paste(line, read_more=lambda: "")
    assert out == "echo one"


def test_coalesce_multi_line_paste() -> None:
    """A paste that spans lines is joined with newlines and end-marker stripped."""
    queue = iter(["echo two", f"echo three{BRACKETED_PASTE_END}"])
    line = f"{BRACKETED_PASTE_BEGIN}echo one"
    out = coalesce_bracketed_paste(line, read_more=lambda: next(queue))
    assert out == "echo one\necho two\necho three"


def test_coalesce_preserves_pre_and_post() -> None:
    """Text before/after the markers is preserved on the same logical line."""
    queue = iter([f"line two{BRACKETED_PASTE_END}AFTER"])
    line = f"BEFORE{BRACKETED_PASTE_BEGIN}line one"
    out = coalesce_bracketed_paste(line, read_more=lambda: next(queue))
    assert out == "BEFOREline one\nline twoAFTER"


def test_coalesce_unterminated_paste_returns_partial() -> None:
    """An EOF mid-paste returns what we got rather than raising."""

    def _read_more() -> str:
        raise EOFError()

    line = f"{BRACKETED_PASTE_BEGIN}half"
    out = coalesce_bracketed_paste(line, read_more=_read_more)
    assert out == "half"


def test_coalesce_empty_paste() -> None:
    """An empty paste body collapses to an empty string."""
    out = coalesce_bracketed_paste(
        f"{BRACKETED_PASTE_BEGIN}{BRACKETED_PASTE_END}",
        read_more=lambda: "",
    )
    assert out == ""


def test_coalesce_paste_with_blank_line() -> None:
    """A blank line inside a paste is preserved as an empty segment."""
    queue = iter(["", f"echo two{BRACKETED_PASTE_END}"])
    line = f"{BRACKETED_PASTE_BEGIN}echo one"
    out = coalesce_bracketed_paste(line, read_more=lambda: next(queue))
    assert out == "echo one\n\necho two"


# ---------------------------------------------------------------------------
# StoatRepl integration
# ---------------------------------------------------------------------------


def _make_repl(
    inputs: list[str],
    *,
    bracketed_paste: bool = True,
    start_in_shell_mode: bool = False,
) -> tuple[StoatRepl, io.StringIO]:
    """Build a REPL whose ``input_fn`` is a scripted iterator."""
    out = io.StringIO()
    iterator = iter(inputs)

    def fake_input(_prompt: str) -> str:
        try:
            return next(iterator)
        except StopIteration as exc:
            raise EOFError() from exc

    repl = StoatRepl(
        model="kimi-k2.6",
        workdir=".",
        max_steps=50,
        out=out,
        input_fn=fake_input,
        bracketed_paste=bracketed_paste,
        hook_emitter=None,  # disable autoload so tests stay hermetic
        start_in_shell_mode=start_in_shell_mode,
    )
    return repl, out


def test_repl_default_enables_bracketed_paste() -> None:
    """The constructor defaults to ``bracketed_paste=True``."""
    repl, _ = _make_repl([])
    assert repl.bracketed_paste is True


def test_repl_disable_bracketed_paste() -> None:
    """``bracketed_paste=False`` opts out (each line dispatches separately)."""
    repl, _ = _make_repl([], bracketed_paste=False)
    assert repl.bracketed_paste is False


def test_repl_read_input_passthrough_when_no_marker() -> None:
    """A regular line is returned unchanged."""
    repl, _ = _make_repl(["hello"])
    line = repl._read_input("> ")
    assert line == "hello"


def test_repl_read_input_coalesces_paste() -> None:
    """A multi-line paste is coalesced into one input."""
    queued = [
        f"{BRACKETED_PASTE_BEGIN}echo one",
        "echo two",
        f"echo three{BRACKETED_PASTE_END}",
    ]
    repl, _ = _make_repl(queued)
    line = repl._read_input("> ")
    assert line == "echo one\necho two\necho three"


def test_repl_read_input_disabled_does_not_coalesce() -> None:
    """With ``bracketed_paste=False`` the markers leak through verbatim."""
    queued = [
        f"{BRACKETED_PASTE_BEGIN}echo one",
        "echo two",
        f"echo three{BRACKETED_PASTE_END}",
    ]
    repl, _ = _make_repl(queued, bracketed_paste=False)
    line = repl._read_input("> ")
    assert BRACKETED_PASTE_BEGIN in line


def test_paste_in_shell_mode_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A multi-line paste in shell mode dispatches as one shell turn."""
    queued = [
        f"{BRACKETED_PASTE_BEGIN}echo one",
        "echo two",
        f"echo three{BRACKETED_PASTE_END}",
        "/exit",
    ]
    repl, out = _make_repl(queued, start_in_shell_mode=True)
    assert repl.shell_mode.mode == MODE_SHELL

    captured: list[str] = []

    def fake_run_shell_turn(self_repl: StoatRepl, command: str) -> str:  # noqa: ARG001
        captured.append(command)
        return f"[shell-turn:{command!r}]"

    monkeypatch.setattr(StoatRepl, "run_shell_turn", fake_run_shell_turn)
    rc = repl.run()
    assert rc == 0
    # Exactly one shell turn for the entire paste.
    assert captured == ["echo one\necho two\necho three"]
    assert "[shell-turn:'echo one\\necho two\\necho three']" in out.getvalue()


def test_paste_with_end_marker_only_partial(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ARG001
    """End marker without a begin marker is left untouched."""
    repl, _ = _make_repl([f"line{BRACKETED_PASTE_END}"])
    line = repl._read_input("> ")
    # No begin marker -> coalescer is a no-op.
    assert line == f"line{BRACKETED_PASTE_END}"


# ---------------------------------------------------------------------------
# Hooks layer wiring sanity
# ---------------------------------------------------------------------------


def test_repl_module_exports_paste_helpers() -> None:
    """The public ``__all__`` advertises the paste helpers."""
    public = set(stoat_repl.__all__)
    assert "BRACKETED_PASTE_BEGIN" in public
    assert "BRACKETED_PASTE_END" in public
    assert "coalesce_bracketed_paste" in public

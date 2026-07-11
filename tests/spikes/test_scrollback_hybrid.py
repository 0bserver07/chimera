"""Deterministic, TTY-free tests for the scrollback-hybrid spike.

The spike (``scripts/spikes/scrollback_hybrid.py``) factors all escape
emission into pure functions; these tests pin the emitted byte sequences
exactly, check the geometry/clamping math, exercise the runtime against a
StringIO "terminal", and run the demo end-to-end as a subprocess with piped
(non-TTY) stdio. Nothing here needs a terminal.
"""
from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("rich")  # spike renders via rich (ships with the tui extra); CI installs none

_SPIKE = Path(__file__).resolve().parents[2] / "scripts" / "spikes" / "scrollback_hybrid.py"
_spec = importlib.util.spec_from_file_location("scrollback_hybrid", _SPIKE)
assert _spec is not None and _spec.loader is not None
sh = importlib.util.module_from_spec(_spec)
sys.modules["scrollback_hybrid"] = sh  # dataclasses need the module resolvable
_spec.loader.exec_module(sh)


# ---------------------------------------------------------------------------
# Sequence builders — byte-exact
# ---------------------------------------------------------------------------


class TestCup:
    def test_bytes(self) -> None:
        assert sh.cup(21, 1) == "\x1b[21;1H"
        assert sh.cup(1, 80) == "\x1b[1;80H"

    def test_one_based_validation(self) -> None:
        with pytest.raises(ValueError):
            sh.cup(0, 1)
        with pytest.raises(ValueError):
            sh.cup(1, 0)


class TestRegion:
    def test_setup_bytes(self) -> None:
        assert sh.region_setup(1, 21) == "\x1b[1;21r"
        assert sh.region_setup(10, 24) == "\x1b[10;24r"

    def test_setup_rejects_degenerate_regions(self) -> None:
        with pytest.raises(ValueError):
            sh.region_setup(0, 5)
        with pytest.raises(ValueError):
            sh.region_setup(5, 5)  # DECSTBM regions are at least two rows
        with pytest.raises(ValueError):
            sh.region_setup(6, 5)

    def test_reset_bytes(self) -> None:
        assert sh.region_reset() == "\x1b[r"


class TestGeometry:
    def test_standard_layout(self) -> None:
        g = sh.Geometry.fit(24, 80, 3)
        assert (g.rows, g.cols, g.band_height) == (24, 80, 3)
        assert g.band_top == 22
        assert g.history_bottom == 21

    def test_band_shrinks_to_keep_two_history_rows(self) -> None:
        g = sh.Geometry.fit(4, 80, 3)
        assert g.band_height == 2
        assert g.history_bottom == 2

    def test_degenerate_screens(self) -> None:
        assert sh.Geometry.fit(2, 80, 3).band_height == 1
        assert sh.Geometry.fit(1, 80, 3).band_height == 1

    def test_cols_floor(self) -> None:
        assert sh.Geometry.fit(24, 3, 3).cols == 10


class TestCommitLines:
    def test_exact_sequence_for_two_lines(self) -> None:
        seq = sh.commit_lines(["one", "two"], history_bottom=21, start_row=21)
        assert seq == (
            "\x1b[?2026h"  # synchronized output begin
            "\x1b7"  # DECSC: save cursor (DECSTBM will home it)
            "\x1b[1;21r"  # region = everything above the band
            "\x1b[21;1H"  # park on the region's bottom row
            "\r\n\x1b[Kone\x1b[0m"  # LF at bottom margin scrolls the region
            "\r\n\x1b[Ktwo\x1b[0m"
            "\x1b[r"  # release the region (homes the cursor)
            "\x1b8"  # DECRC: cursor-neutral overall
            "\x1b[?2026l"
        )

    def test_empty_and_too_small_history(self) -> None:
        assert sh.commit_lines([], history_bottom=21, start_row=21) == ""
        assert sh.commit_lines(["x"], history_bottom=1, start_row=1) == ""
        assert sh.commit_lines(["x"], history_bottom=0, start_row=1) == ""

    def test_start_row_clamped_into_region(self) -> None:
        seq = sh.commit_lines(["x"], history_bottom=21, start_row=99)
        assert "\x1b[21;1H" in seq
        seq = sh.commit_lines(["x"], history_bottom=21, start_row=0)
        assert "\x1b[1;1H" in seq

    def test_line_order_and_one_linefeed_per_line(self) -> None:
        lines = [f"line-{i}" for i in range(5)]
        seq = sh.commit_lines(lines, history_bottom=21, start_row=21)
        assert seq.count("\r\n") == 5
        positions = [seq.index(line) for line in lines]
        assert positions == sorted(positions)


class TestMakeRoom:
    def test_reverse_index_count_and_new_top(self) -> None:
        seq, new_top = sh.make_room(10, 3, 24, wanted=5)
        assert new_top == 15
        assert "\x1b[10;24r" in seq  # region = band .. screen bottom
        assert "\x1b[10;1H" in seq  # cursor at region TOP
        assert seq.count("\x1bM") == 5  # one reverse index per freed row
        assert seq.index("\x1b[10;24r") < seq.index("\x1bM") < seq.index("\x1b[r")
        assert seq.startswith("\x1b7") and seq.endswith("\x1b8")

    def test_clamped_by_space_below(self) -> None:
        seq, new_top = sh.make_room(10, 3, 24, wanted=50)
        assert new_top == 22  # band bottom lands on row 24
        assert seq.count("\x1bM") == 12

    def test_noop_when_already_at_bottom_or_nothing_wanted(self) -> None:
        assert sh.make_room(22, 3, 24, wanted=5) == ("", 22)
        assert sh.make_room(10, 3, 24, wanted=0) == ("", 10)


class TestInitialBandPosition:
    def test_fits_at_cursor(self) -> None:
        assert sh.initial_band_position(5, 24, 3) == ("", 5)

    def test_overflow_scrolls_whole_screen(self) -> None:
        seq, top = sh.initial_band_position(23, 24, 3)
        assert top == 22
        assert seq == "\x1b[24;1H\n"  # one plain LF per overflowing row
        seq, top = sh.initial_band_position(24, 24, 3)
        assert top == 22
        assert seq == "\x1b[24;1H\n\n"

    def test_cursor_row_clamped(self) -> None:
        _, top = sh.initial_band_position(99, 24, 3)
        assert top == 22


class TestBandPaint:
    def test_rows_els_park_and_cursor_visibility(self) -> None:
        seq = sh.band_paint(["AAA", "BBB"], 22, park=(23, 5))
        assert seq.startswith("\x1b[?2026h\x1b[?25l")
        assert "\x1b[22;1H\x1b[KAAA\x1b[0m" in seq
        assert "\x1b[23;1H\x1b[KBBB\x1b[0m" in seq
        assert seq.endswith("\x1b[23;5H\x1b[?25h\x1b[?2026l")

    def test_never_scrolls(self) -> None:
        seq = sh.band_paint(["x"], 22, park=(22, 1))
        assert "\n" not in seq and "\x1bM" not in seq


class TestResizeReglue:
    def test_grow(self) -> None:
        seq, new_top = sh.resize_reglue(22, sh.Geometry.fit(30, 100, 3))
        assert new_top == 28
        assert seq == "\x1b[r\x1b[22;1H\x1b[0J"  # clear from the OLD top down

    def test_shrink(self) -> None:
        seq, new_top = sh.resize_reglue(22, sh.Geometry.fit(20, 60, 3))
        assert new_top == 18
        assert seq == "\x1b[r\x1b[18;1H\x1b[0J"  # clear from the NEW top down


class TestExitAndRestore:
    def test_exit_sequence(self) -> None:
        assert sh.exit_seq(22) == "\x1b[r\x1b[22;1H\x1b[0J\x1b[0m\x1b[?25h"

    def test_exit_clamps_band_top(self) -> None:
        assert "\x1b[1;1H" in sh.exit_seq(0)

    def test_emergency_restore_is_minimal_and_nondestructive(self) -> None:
        seq = sh.emergency_restore_seq()
        assert seq == "\x1b[r\x1b[0m\x1b[?25h"
        assert "\x1b[0J" not in seq and "J" not in seq.replace("\x1b[?25h", "")


# ---------------------------------------------------------------------------
# Width handling
# ---------------------------------------------------------------------------


class TestWidth:
    def test_strip_ansi_and_visible_cells(self) -> None:
        assert sh.strip_ansi("\x1b[31mab\x1b[0m") == "ab"
        assert sh.strip_ansi("\x1b7x\x1b8\x1bM") == "x"
        assert sh.visible_cells("\x1b[1mab\x1b[0m你") == 4  # CJK char = 2 cells

    def test_hard_wrap_ascii(self) -> None:
        assert sh.hard_wrap_cells("abcdef", 3) == ["abc", "def"]
        assert sh.hard_wrap_cells("", 10) == [""]

    def test_hard_wrap_never_splits_wide_chars(self) -> None:
        lines = sh.hard_wrap_cells("你好你好", 5)  # each char 2 cells
        assert all(sh.visible_cells(line) <= 5 for line in lines)
        assert "".join(lines) == "你好你好"

    def test_render_ansi_lines_respects_width(self) -> None:
        md = sh.Markdown(
            "# Title\n\nSome prose with a fairly long sentence that must wrap, "
            "plus 宽字符文本 and `inline code`.\n"
        )
        for width in (24, 40, 79):
            lines = sh.render_ansi_lines(md, width)
            assert lines, "renderer produced no lines"
            assert all(sh.visible_cells(line) <= width for line in lines)

    def test_fallback_split_blocks(self) -> None:
        blocks, tail = sh._fallback_split("a\n\nb\n\nc-tail")
        assert blocks == ["a\n\n", "b\n\n"]
        assert tail == "c-tail"


# ---------------------------------------------------------------------------
# Runtime against a StringIO terminal (no TTY anywhere)
# ---------------------------------------------------------------------------


def _make_screen() -> "tuple[sh.HybridScreen, io.StringIO]":  # type: ignore[name-defined]
    out = io.StringIO()
    screen = sh.HybridScreen(out, band_height=3, assume_bottom=True)
    return screen, out


class TestHybridScreenRuntime:
    def test_commit_writes_the_pure_sequence(self) -> None:
        screen, out = _make_screen()
        screen.start()
        try:
            screen.commit(["hello", "world"])
            expected = sh.commit_lines(["hello", "world"], history_bottom=21, start_row=21)
            assert expected in out.getvalue()
            assert screen.committed == 2
        finally:
            screen.stop()

    def test_commit_from_midscreen_makes_room_first(self) -> None:
        screen, out = _make_screen()
        screen.start()
        try:
            screen.band_top = 10  # simulate the shell having left the cursor mid-screen
            screen.commit(["a", "b"])
            text = out.getvalue()
            assert screen.band_top == 12
            assert "\x1b[10;24r" in text  # make-room region: band .. bottom
            assert text.count("\x1bM") == 2
            # history insert starts on the last committed row (old band top - 1)
            assert "\x1b[1;11r" in text and "\x1b[9;1H" in text
        finally:
            screen.stop()

    def test_resize_reglues_band_to_bottom(self) -> None:
        screen, out = _make_screen()
        screen.start()
        try:
            screen._probe_size = lambda: (30, 100)  # type: ignore[method-assign]
            screen._resized = True
            assert screen.handle_resize() is True
            assert screen.geom.rows == 30
            assert screen.band_top == 28
            assert "\x1b[r\x1b[22;1H\x1b[0J" in out.getvalue()
        finally:
            screen.stop()

    def test_stop_erases_band_and_restores(self) -> None:
        screen, out = _make_screen()
        screen.start()
        screen.stop()
        text = out.getvalue()
        assert sh.exit_seq(22) in text
        screen.stop()  # idempotent
        assert out.getvalue() == text

    def test_paint_band_parks_cursor_on_composer_row(self) -> None:
        screen, out = _make_screen()
        screen.start()
        try:
            screen.paint_band(["sep", "composer", "status"], park_col=7)
            assert "\x1b[23;7H\x1b[?25h" in out.getvalue()  # row 2 of the band
        finally:
            screen.stop()


# ---------------------------------------------------------------------------
# End-to-end subprocess runs (piped stdio — still no TTY)
# ---------------------------------------------------------------------------


def _run_spike(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SPIKE), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=_SPIKE.parents[2],
    )


class TestEndToEnd:
    def test_help_exits_zero(self) -> None:
        proc = _run_spike("--help")
        assert proc.returncode == 0
        assert "native-scrollback" in proc.stdout

    def test_clean_run_commits_and_restores(self) -> None:
        proc = _run_spike("--rows", "8", "--delay", "0", "--linger", "0", "--assume-bottom")
        assert proc.returncode == 0
        out = proc.stdout
        assert "\x1b[1;21r" in out  # commits target the 80x24 fallback geometry
        assert sh.exit_seq(22) in out  # clean shutdown erased the band
        visible = sh.strip_ansi(out)
        assert "Native-scrollback hybrid" in visible  # first committed heading
        assert "lines committed to native scrollback" in visible
        # every region set is eventually released
        assert out.count("\x1b[1;21r") <= out.count("\x1b[r")

    def test_crash_run_restores_before_traceback(self) -> None:
        proc = _run_spike(
            "--rows", "30", "--delay", "0", "--linger", "0", "--assume-bottom",
            "--crash-at", "3",
        )
        assert proc.returncode == 1
        assert "deliberate crash" in proc.stderr
        assert sh.emergency_restore_seq() in proc.stdout
        # the clean-exit epilogue must NOT have run — this exercised the
        # excepthook/atexit path, not stop()
        assert "lines committed to native scrollback" not in proc.stdout

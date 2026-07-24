"""Deterministic, TTY-free tests for the productized native-scrollback module.

``chimera/tui/scrollback.py`` is the productized version of the spike
``scripts/spikes/scrollback_hybrid.py``; its byte-exact builder tests are
adapted from ``tests/spikes/test_scrollback_hybrid.py`` (which stays as the
spike's lineage record). This file additionally pins the productized additions:
the capability gate (:func:`inline_capability`) and the state-restoration
guarantees on :class:`HybridScreen`.

The module is stdlib-only, so — unlike the rich-rendering frontend — these
tests need no ``pytest.importorskip`` and run under CI's no-extras posture.
"""
from __future__ import annotations

import contextlib
import io
import sys
from collections.abc import Iterator

import pytest

from chimera.tui import scrollback as sb


@contextlib.contextmanager
def _started(screen: sb.HybridScreen) -> Iterator[None]:
    """Run a started screen with guaranteed hook teardown.

    ``start()`` swaps ``sys.excepthook`` and the SIGTERM/SIGWINCH handlers and
    registers an atexit; after the crash path ``stop()`` early-returns without
    restoring them. This unconditionally restores every hook so one test's
    started screen can never poison the next.
    """
    screen.start()
    try:
        yield
    finally:
        screen._teardown_hooks()

# ---------------------------------------------------------------------------
# Sequence builders — byte-exact (adapted from the spike suite)
# ---------------------------------------------------------------------------


class TestCup:
    def test_bytes(self) -> None:
        assert sb.cup(21, 1) == "\x1b[21;1H"
        assert sb.cup(1, 80) == "\x1b[1;80H"

    def test_one_based_validation(self) -> None:
        with pytest.raises(ValueError):
            sb.cup(0, 1)
        with pytest.raises(ValueError):
            sb.cup(1, 0)


class TestRegion:
    def test_setup_bytes(self) -> None:
        assert sb.region_setup(1, 21) == "\x1b[1;21r"
        assert sb.region_setup(10, 24) == "\x1b[10;24r"

    def test_setup_rejects_degenerate_regions(self) -> None:
        with pytest.raises(ValueError):
            sb.region_setup(0, 5)
        with pytest.raises(ValueError):
            sb.region_setup(5, 5)  # DECSTBM regions are at least two rows
        with pytest.raises(ValueError):
            sb.region_setup(6, 5)

    def test_reset_bytes(self) -> None:
        assert sb.region_reset() == "\x1b[r"


class TestGeometry:
    def test_standard_layout(self) -> None:
        g = sb.Geometry.fit(24, 80, 3)
        assert (g.rows, g.cols, g.band_height) == (24, 80, 3)
        assert g.band_top == 22
        assert g.history_bottom == 21

    def test_band_shrinks_to_keep_two_history_rows(self) -> None:
        g = sb.Geometry.fit(4, 80, 3)
        assert g.band_height == 2
        assert g.history_bottom == 2

    def test_degenerate_screens(self) -> None:
        assert sb.Geometry.fit(2, 80, 3).band_height == 1
        assert sb.Geometry.fit(1, 80, 3).band_height == 1

    def test_cols_floor(self) -> None:
        assert sb.Geometry.fit(24, 3, 3).cols == 10


class TestCommitLines:
    def test_exact_sequence_for_two_lines(self) -> None:
        seq = sb.commit_lines(["one", "two"], history_bottom=21, start_row=21)
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
        assert sb.commit_lines([], history_bottom=21, start_row=21) == ""
        assert sb.commit_lines(["x"], history_bottom=1, start_row=1) == ""
        assert sb.commit_lines(["x"], history_bottom=0, start_row=1) == ""

    def test_start_row_clamped_into_region(self) -> None:
        seq = sb.commit_lines(["x"], history_bottom=21, start_row=99)
        assert "\x1b[21;1H" in seq
        seq = sb.commit_lines(["x"], history_bottom=21, start_row=0)
        assert "\x1b[1;1H" in seq

    def test_line_order_and_one_linefeed_per_line(self) -> None:
        lines = [f"line-{i}" for i in range(5)]
        seq = sb.commit_lines(lines, history_bottom=21, start_row=21)
        assert seq.count("\r\n") == 5
        positions = [seq.index(line) for line in lines]
        assert positions == sorted(positions)


class TestMakeRoom:
    def test_reverse_index_count_and_new_top(self) -> None:
        seq, new_top = sb.make_room(10, 3, 24, wanted=5)
        assert new_top == 15
        assert "\x1b[10;24r" in seq  # region = band .. screen bottom
        assert "\x1b[10;1H" in seq  # cursor at region TOP
        assert seq.count("\x1bM") == 5  # one reverse index per freed row
        assert seq.index("\x1b[10;24r") < seq.index("\x1bM") < seq.index("\x1b[r")
        assert seq.startswith("\x1b7") and seq.endswith("\x1b8")

    def test_clamped_by_space_below(self) -> None:
        seq, new_top = sb.make_room(10, 3, 24, wanted=50)
        assert new_top == 22  # band bottom lands on row 24
        assert seq.count("\x1bM") == 12

    def test_noop_when_already_at_bottom_or_nothing_wanted(self) -> None:
        assert sb.make_room(22, 3, 24, wanted=5) == ("", 22)
        assert sb.make_room(10, 3, 24, wanted=0) == ("", 10)


class TestInitialBandPosition:
    def test_fits_at_cursor(self) -> None:
        assert sb.initial_band_position(5, 24, 3) == ("", 5)

    def test_overflow_scrolls_whole_screen(self) -> None:
        seq, top = sb.initial_band_position(23, 24, 3)
        assert top == 22
        assert seq == "\x1b[24;1H\n"  # one plain LF per overflowing row
        seq, top = sb.initial_band_position(24, 24, 3)
        assert top == 22
        assert seq == "\x1b[24;1H\n\n"

    def test_cursor_row_clamped(self) -> None:
        _, top = sb.initial_band_position(99, 24, 3)
        assert top == 22


class TestBandPaint:
    def test_rows_els_park_and_cursor_visibility(self) -> None:
        seq = sb.band_paint(["AAA", "BBB"], 22, park=(23, 5))
        assert seq.startswith("\x1b[?2026h\x1b[?25l")
        assert "\x1b[22;1H\x1b[KAAA\x1b[0m" in seq
        assert "\x1b[23;1H\x1b[KBBB\x1b[0m" in seq
        assert seq.endswith("\x1b[23;5H\x1b[?25h\x1b[?2026l")

    def test_never_scrolls(self) -> None:
        seq = sb.band_paint(["x"], 22, park=(22, 1))
        assert "\n" not in seq and "\x1bM" not in seq


class TestResizeReglue:
    def test_grow(self) -> None:
        seq, new_top = sb.resize_reglue(22, sb.Geometry.fit(30, 100, 3))
        assert new_top == 28
        assert seq == "\x1b[r\x1b[22;1H\x1b[0J"  # clear from the OLD top down

    def test_shrink(self) -> None:
        seq, new_top = sb.resize_reglue(22, sb.Geometry.fit(20, 60, 3))
        assert new_top == 18
        assert seq == "\x1b[r\x1b[18;1H\x1b[0J"  # clear from the NEW top down


class TestExitAndRestore:
    def test_exit_sequence(self) -> None:
        assert sb.exit_seq(22) == "\x1b[r\x1b[22;1H\x1b[0J\x1b[0m\x1b[?25h"

    def test_exit_clamps_band_top(self) -> None:
        assert "\x1b[1;1H" in sb.exit_seq(0)

    def test_emergency_restore_is_minimal_and_nondestructive(self) -> None:
        seq = sb.emergency_restore_seq()
        assert seq == "\x1b[r\x1b[0m\x1b[?25h"
        # non-destructive: no erase-display, and no clear beyond showing cursor
        assert "\x1b[0J" not in seq and "J" not in seq.replace("\x1b[?25h", "")


class TestStripAnsi:
    def test_removes_csi_osc_and_single_escapes(self) -> None:
        assert sb.strip_ansi("\x1b[31mab\x1b[0m") == "ab"
        assert sb.strip_ansi("\x1b7x\x1b8\x1bM") == "x"
        assert sb.strip_ansi("plain") == "plain"


# ---------------------------------------------------------------------------
# Capability gate — the mandatory multiplexer / POSIX / TTY refusal
# ---------------------------------------------------------------------------


class TestInlineCapability:
    def _ok(self, **over: object) -> dict[str, object]:
        base: dict[str, object] = {
            "platform": "linux",
            "stdout_isatty": True,
            "stdin_isatty": True,
            "env": {},
        }
        base.update(over)
        return base

    def test_not_requested_is_silent_disabled(self) -> None:
        d = sb.inline_capability(False, **self._ok())
        assert d.use_inline is False
        assert d.reason == "disabled"
        assert d.refused is False  # a plain off state prints no note

    def test_happy_path(self) -> None:
        d = sb.inline_capability(True, **self._ok())
        assert d.use_inline is True
        assert d.reason == "inline"
        assert d.refused is False

    def test_windows_refused_non_posix(self) -> None:
        d = sb.inline_capability(True, **self._ok(platform="win32"))
        assert d.use_inline is False
        assert d.reason == "non-posix"
        assert d.refused is True

    @pytest.mark.parametrize("out,inp", [(False, True), (True, False), (False, False)])
    def test_non_tty_refused(self, out: bool, inp: bool) -> None:
        d = sb.inline_capability(True, **self._ok(stdout_isatty=out, stdin_isatty=inp))
        assert d.use_inline is False
        assert d.reason == "not-a-tty"
        assert d.refused is True

    def test_scrollback_hostile_multiplexer_refused_and_named(self) -> None:
        # The one hard failure the spike proved: a multiplexer that drops
        # partial-scroll-region evictions. Detected by its session env var,
        # reported verbatim (never editorialized), and never silently used.
        d = sb.inline_capability(True, **self._ok(env={"ZELLIJ": "0"}))
        assert d.use_inline is False
        assert d.reason == "multiplexer:ZELLIJ"
        assert d.refused is True
        assert "ZELLIJ" in sb.SCROLLBACK_HOSTILE_ENV_VARS

    def test_empty_hostile_var_does_not_trip(self) -> None:
        # An empty value is not an active session — do not refuse on it.
        d = sb.inline_capability(True, **self._ok(env={"ZELLIJ": ""}))
        assert d.use_inline is True


# ---------------------------------------------------------------------------
# HybridScreen runtime against a StringIO "terminal" (no TTY anywhere)
# ---------------------------------------------------------------------------


def _make_screen() -> tuple[sb.HybridScreen, io.StringIO]:
    out = io.StringIO()
    screen = sb.HybridScreen(out, band_height=3, assume_bottom=True)
    return screen, out


class TestHybridScreenRuntime:
    def test_probe_falls_back_for_stringio(self) -> None:
        screen, _ = _make_screen()
        # StringIO has no real winsize → the safe 24x80 fallback.
        assert (screen.geom.rows, screen.geom.cols) == (24, 80)

    def test_commit_writes_the_pure_sequence(self) -> None:
        screen, out = _make_screen()
        with _started(screen):
            screen.commit(["hello", "world"])
            expected = sb.commit_lines(["hello", "world"], history_bottom=21, start_row=21)
            assert expected in out.getvalue()
            assert screen.committed == 2

    def test_commit_from_midscreen_makes_room_first(self) -> None:
        screen, out = _make_screen()
        with _started(screen):
            screen.band_top = 10  # simulate the shell having left the cursor mid-screen
            screen.commit(["a", "b"])
            text = out.getvalue()
            assert screen.band_top == 12
            assert "\x1b[10;24r" in text  # make-room region: band .. bottom
            assert text.count("\x1bM") == 2
            # history insert starts on the last committed row (old band top - 1)
            assert "\x1b[1;11r" in text and "\x1b[9;1H" in text

    def test_resize_reglues_band_to_bottom(self) -> None:
        screen, out = _make_screen()
        with _started(screen):
            screen._probe_size = lambda: (30, 100)  # type: ignore[method-assign]
            screen._resized = True
            assert screen.handle_resize() is True
            assert screen.geom.rows == 30
            assert screen.band_top == 28
            assert "\x1b[r\x1b[22;1H\x1b[0J" in out.getvalue()

    def test_sigwinch_flag_drives_next_resize(self) -> None:
        screen, _ = _make_screen()
        assert screen.handle_resize() is False  # nothing pending
        screen._on_sigwinch(28, None)  # the tiny signal handler just flags
        screen._probe_size = lambda: (40, 120)  # type: ignore[method-assign]
        assert screen.handle_resize() is True
        assert screen.geom.rows == 40

    def test_paint_band_parks_cursor_on_composer_row(self) -> None:
        screen, out = _make_screen()
        with _started(screen):
            screen.paint_band(["sep", "composer", "status"], park_col=7)
            assert "\x1b[23;7H\x1b[?25h" in out.getvalue()  # row 2 of the band

    def test_read_available_without_tty_is_empty(self) -> None:
        screen, _ = _make_screen()
        assert screen.stdin_fd is None  # StringIO stdin path → no interactive fd
        assert screen.read_available() == b""


# ---------------------------------------------------------------------------
# State-restoration guarantees (the spike's core promise, carried here)
# ---------------------------------------------------------------------------


class TestStateRestoration:
    def test_stop_erases_band_and_is_idempotent(self) -> None:
        screen, out = _make_screen()
        with _started(screen):
            screen.stop()
            text = out.getvalue()
            assert sb.exit_seq(22) in text  # clean shutdown erased the band chrome
            screen.stop()  # idempotent
            assert out.getvalue() == text

    def test_start_swaps_hooks_and_stop_restores_them(self) -> None:
        original = sys.excepthook
        screen, _ = _make_screen()
        with _started(screen):
            assert sys.excepthook is not original  # crash hook armed
            screen.stop()  # a clean exit restores hooks itself
            assert sys.excepthook is original
        assert sys.excepthook is original  # and the teardown net keeps it there

    def test_emergency_restore_is_nondestructive_and_wins_over_stop(self) -> None:
        screen, out = _make_screen()
        with _started(screen):
            screen._emergency_restore()  # simulates the crash / atexit / SIGTERM path
            text = out.getvalue()
            # restored the region + SGR + cursor, but did NOT erase the screen
            # (the transcript and an about-to-print traceback stay intact)
            assert sb.emergency_restore_seq() in text
            assert sb.exit_seq(22) not in text
            # a subsequent clean stop() is a no-op: already restored
            screen.stop()
            assert out.getvalue() == text

    def test_crash_hook_restores_before_delegating_to_old_excepthook(self) -> None:
        seen: list[str] = []
        screen, out = _make_screen()
        with _started(screen):
            # Spy the downstream excepthook the crash hook must still call,
            # then restore the true original before the teardown net runs.
            real_old = screen._old_excepthook
            screen._old_excepthook = lambda *a: seen.append("old-hook-ran")  # type: ignore[assignment]
            try:
                screen._crash_hook(RuntimeError, RuntimeError("boom"), None)
                # terminal restored (emergency prefix present) BEFORE the traceback
                assert sb.emergency_restore_seq() in out.getvalue()
                assert seen == ["old-hook-ran"]
            finally:
                screen._old_excepthook = real_old

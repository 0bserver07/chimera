#!/usr/bin/env python3
"""Spike: native-scrollback hybrid transcript renderer (R-VIEW-5).

A standalone prototype of the DECSTBM scroll-region hybrid from the reference
corpus (R1's model): finished transcript lines are pushed *above* a reserved
bottom band into the terminal's own scrollback — where native selection, copy,
and wheel-scroll keep working — while the band (composer + status) repaints in
place using plain cursor addressing. No alternate screen, no mouse capture,
no Textual.

Mechanism (all sequences are classic VT100/VT220, 1-based rows):

- ``ESC[{top};{bottom}r`` (DECSTBM) confines scrolling to a region. It is set
  *transiently* around each operation, never left active.
- Committing N lines: set the region to everything above the band, park the
  cursor on the region's bottom row, and emit ``\\r\\n`` + line, N times. Each
  linefeed at the region bottom scrolls the region up by one; the row evicted
  at the top of the screen enters the terminal's scrollback.
- When the band is not yet glued to the screen bottom (fresh shell mid-screen),
  the band is first scrolled *down* into place: region = band..bottom, cursor
  at the region top, ``ESC M`` (reverse index) once per freed row.
- ``ESC[r`` resets the region (this homes the cursor, hence DECSC/DECRC or an
  explicit CUP around every region operation).
- The band repaint never touches the region: it is CUP + EL per band row.

The demo plays a scripted fake agent stream (markdown prose, a code fence,
tool lines, wide CJK text, a long URL) through a newline-gated block splitter
into the scrollback region, while a 3-row band shows a fake composer (echoes
real keystrokes when run on a TTY) and a ticking status row.

Run it on a real terminal::

    uv run python scripts/spikes/scrollback_hybrid.py --rows 60

After exit the transcript remains in the terminal's scrollback, selectable
with the mouse, and the shell prompt resumes directly below it.

Flags: ``--rows N`` fake-stream length (committed-line target), ``--delay MS``
inter-chunk pacing, ``--band-height N``, ``--crash-at N`` (raise mid-stream to
prove crash restoration), ``--assume-bottom`` (skip the cursor-position query).

Escape emission is factored into pure functions (byte-exact unit tests in
``tests/spikes/test_scrollback_hybrid.py``); only ``HybridScreen`` touches the
TTY. Dependencies: stdlib + rich.
"""
from __future__ import annotations

import argparse
import atexit
import contextlib
import os
import re
import select
import signal
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from types import FrameType, TracebackType
from typing import IO, Any

from rich.cells import cell_len, chop_cells
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

# --------------------------------------------------------------------------
# Escape-sequence vocabulary (see docs/specs/tui-scrollback-hybrid.md)
# --------------------------------------------------------------------------

ESC = "\x1b"
CSI = ESC + "["
DECSC = ESC + "7"  # save cursor position + SGR + charset
DECRC = ESC + "8"  # restore what DECSC saved
REVERSE_INDEX = ESC + "M"  # RI: cursor up; at region top, scroll region down
REGION_RESET = CSI + "r"  # DECSTBM with no params: margins = full screen
SYNC_BEGIN = CSI + "?2026h"  # synchronized output: composite atomically
SYNC_END = CSI + "?2026l"
HIDE_CURSOR = CSI + "?25l"
SHOW_CURSOR = CSI + "?25h"
SGR_RESET = CSI + "0m"
CLEAR_TO_EOL = CSI + "K"  # EL 0: erase cursor -> end of line
CLEAR_TO_EOS = CSI + "0J"  # ED 0: erase cursor -> end of screen
CPR_QUERY = CSI + "6n"  # DSR 6: terminal replies ESC[{row};{col}R

_CPR_RE = re.compile(r"\x1b\[(\d+);(\d+)R")
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI sequences
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequences
    r"|\x1b[78M]"  # DECSC / DECRC / RI
)


def strip_ansi(text: str) -> str:
    """Remove the escape sequences this module emits from ``text``.

    Args:
        text: A string possibly containing CSI/OSC/ESC sequences.

    Returns:
        The visible characters only.
    """
    return _ANSI_RE.sub("", text)


def visible_cells(text: str) -> int:
    """Return the terminal cell width of ``text`` ignoring escape sequences."""
    return cell_len(strip_ansi(text))


# --------------------------------------------------------------------------
# Pure sequence builders
# --------------------------------------------------------------------------


def cup(row: int, col: int) -> str:
    """Build a CUP (cursor position) sequence.

    Args:
        row: 1-based screen row.
        col: 1-based screen column.

    Returns:
        ``ESC[{row};{col}H``.

    Raises:
        ValueError: If row or col is < 1.
    """
    if row < 1 or col < 1:
        raise ValueError(f"cup is 1-based, got ({row}, {col})")
    return f"{CSI}{row};{col}H"


def region_setup(top: int, bottom: int) -> str:
    """Build a DECSTBM sequence confining scrolling to rows ``top..bottom``.

    Side effect at the terminal: the cursor is homed, so callers must save
    (DECSC) or reposition (CUP) afterwards.

    Args:
        top: 1-based first row of the scroll region.
        bottom: 1-based last row of the scroll region (inclusive).

    Returns:
        ``ESC[{top};{bottom}r``.

    Raises:
        ValueError: If the region is empty or upside down.
    """
    if top < 1 or bottom <= top:
        raise ValueError(f"scroll region needs 1 <= top < bottom, got {top}..{bottom}")
    return f"{CSI}{top};{bottom}r"


def region_reset() -> str:
    """Build the DECSTBM reset (margins back to the full screen).

    Also homes the cursor, like :func:`region_setup`.

    Returns:
        ``ESC[r``.
    """
    return REGION_RESET


@dataclass(frozen=True)
class Geometry:
    """Screen geometry with a bottom band reserved.

    Attributes:
        rows: Total screen rows.
        cols: Total screen columns.
        band_height: Rows reserved for the bottom band.
    """

    rows: int
    cols: int
    band_height: int

    @classmethod
    def fit(cls, rows: int, cols: int, band_height: int) -> Geometry:
        """Clamp a requested layout to what the screen can hold.

        The band shrinks before the history region disappears: two history
        rows are kept whenever the screen allows, because DECSTBM regions are
        at least two rows — a one-row history region cannot scroll at all.

        Args:
            rows: Reported screen rows (>= 1 assumed).
            cols: Reported screen columns.
            band_height: Requested band rows.

        Returns:
            A Geometry with ``band_height <= rows - 2`` when ``rows >= 3``.
        """
        rows = max(rows, 1)
        cols = max(cols, 10)
        band = max(1, min(band_height, rows - 2)) if rows >= 3 else 1
        return cls(rows=rows, cols=cols, band_height=band)

    @property
    def band_top(self) -> int:
        """1-based row of the band's top when glued to the screen bottom."""
        return self.rows - self.band_height + 1

    @property
    def history_bottom(self) -> int:
        """1-based last row of the history region when the band is glued.

        Zero when the band covers the whole screen (degenerate terminals).
        """
        return self.rows - self.band_height


def commit_lines(lines: Sequence[str], *, history_bottom: int, start_row: int) -> str:
    """Build the batch that pushes finished lines into native scrollback.

    Sets the scroll region to everything above the band, parks the cursor on
    ``start_row`` (the last row already holding committed content), and emits
    ``\\r\\n`` + EL + line per entry. Every linefeed on the region's bottom row
    scrolls the region up by one, evicting the top row into scrollback. The
    whole batch is cursor-neutral (DECSC/DECRC) and wrapped in synchronized
    output so partially drawn frames are never visible.

    Callers guarantee each line's visible width fits the screen — a wider line
    would auto-wrap and consume extra region scrolls the caller did not count.

    Args:
        lines: Pre-wrapped, possibly ANSI-styled lines (no newlines inside).
        history_bottom: 1-based last row of the history region (band top - 1).
        start_row: 1-based row writing starts *below* (usually equal to
            ``history_bottom``; smaller right after :func:`make_room` freed
            blank rows).

    Returns:
        The escape/byte sequence, or ``""`` when there is nothing to commit
        or the history region is too small to scroll (DECSTBM regions are at
        least two rows).
    """
    if not lines or history_bottom < 2:
        return ""
    start_row = max(1, min(start_row, history_bottom))
    parts: list[str] = [
        SYNC_BEGIN,
        DECSC,
        region_setup(1, history_bottom),
        cup(start_row, 1),
    ]
    for line in lines:
        parts += ["\r\n", CLEAR_TO_EOL, line, SGR_RESET]
    parts += [region_reset(), DECRC, SYNC_END]
    return "".join(parts)


def make_room(band_top: int, band_height: int, rows: int, wanted: int) -> tuple[str, int]:
    """Scroll a mid-screen band toward the bottom to free rows for history.

    R1's maneuver for a band that is not yet glued to the screen bottom (the
    shell left the cursor mid-screen): set the region from the band's top to
    the screen bottom, park the cursor on the region's *top* row, and emit one
    reverse index (``ESC M``) per freed row — each scrolls the region *down*,
    moving the band toward the bottom and leaving blank rows above it.

    Args:
        band_top: Current 1-based top row of the band.
        band_height: Band rows.
        rows: Total screen rows.
        wanted: Rows of history about to be committed.

    Returns:
        ``(sequence, new_band_top)``; the sequence is ``""`` when the band is
        already at the bottom or nothing was wanted.
    """
    band_bottom = band_top + band_height - 1
    space_below = rows - band_bottom
    scroll = min(wanted, space_below)
    if scroll <= 0 or band_top >= rows:
        return "", band_top
    seq = "".join(
        [
            DECSC,
            region_setup(band_top, rows),
            cup(band_top, 1),
            REVERSE_INDEX * scroll,
            region_reset(),
            DECRC,
        ]
    )
    return seq, band_top + scroll


def initial_band_position(cursor_row: int, rows: int, band_height: int) -> tuple[str, int]:
    """Place the band at the shell's cursor row, scrolling if it won't fit.

    When the cursor sits low enough that ``band_height`` rows would run past
    the screen bottom, the *whole* screen is scrolled up by the overflow
    (plain linefeeds from the bottom row — these push shell history into
    scrollback, never erase it).

    Args:
        cursor_row: 1-based row where the shell left the cursor.
        rows: Total screen rows.
        band_height: Band rows.

    Returns:
        ``(sequence, band_top)``.
    """
    cursor_row = max(1, min(cursor_row, rows))
    overflow = cursor_row + band_height - 1 - rows
    if overflow <= 0:
        return "", cursor_row
    return cup(rows, 1) + "\n" * overflow, max(1, rows - band_height + 1)


def band_paint(
    band_lines: Sequence[str],
    band_top: int,
    park: tuple[int, int],
) -> str:
    """Repaint the bottom band in place.

    Plain cursor addressing — CUP + EL + content per row — inside synchronized
    output, with the cursor hidden during the repaint and parked at ``park``
    (the composer's insertion point) afterwards. Never touches the history
    region and never scrolls.

    Args:
        band_lines: One pre-fit (<= screen width) string per band row.
        band_top: 1-based top row of the band.
        park: 1-based ``(row, col)`` to leave the visible cursor at.

    Returns:
        The escape/byte sequence.
    """
    parts: list[str] = [SYNC_BEGIN, HIDE_CURSOR]
    for i, line in enumerate(band_lines):
        parts += [cup(band_top + i, 1), CLEAR_TO_EOL, line, SGR_RESET]
    parts += [cup(*park), SHOW_CURSOR, SYNC_END]
    return "".join(parts)


def resize_reglue(old_band_top: int, geom: Geometry) -> tuple[str, int]:
    """Re-glue the band to the bottom after a terminal resize.

    Committed rows already in scrollback are terminal-owned and cannot be
    reflowed from here; the recoverable part is the band. Any stale band
    pixels are cleared from the higher of the old/new positions down, and the
    band is repositioned at the new bottom. (R1 re-derives wrapped history
    from source on resize; out of scope for this spike.)

    Args:
        old_band_top: 1-based band top before the resize (clamped if the
            screen shrank past it).
        geom: The new screen geometry.

    Returns:
        ``(sequence, new_band_top)``. The caller repaints the band after.
    """
    new_top = geom.band_top
    clear_from = max(1, min(old_band_top, new_top, geom.rows))
    seq = region_reset() + cup(clear_from, 1) + CLEAR_TO_EOS
    return seq, new_top


def exit_seq(band_top: int) -> str:
    """Build the clean-shutdown sequence.

    Resets the scroll region, erases the band (it is chrome, not transcript),
    restores SGR and cursor visibility, and leaves the cursor at the band's
    old top row — the shell prompt resumes directly under the last committed
    line, with the whole transcript above it in native scrollback.

    Args:
        band_top: 1-based top row of the band being torn down.

    Returns:
        The escape/byte sequence.
    """
    return region_reset() + cup(max(1, band_top), 1) + CLEAR_TO_EOS + SGR_RESET + SHOW_CURSOR


def emergency_restore_seq() -> str:
    """Build the crash-path restoration prefix.

    The minimum that makes a terminal usable again regardless of what was
    active when the process died: reset the scroll region, restore SGR,
    show the cursor. Deliberately does not erase or move anything — a
    traceback about to print must stay readable and the transcript intact.

    Returns:
        The escape/byte sequence.
    """
    return region_reset() + SGR_RESET + SHOW_CURSOR


def hard_wrap_cells(text: str, width: int) -> list[str]:
    """Hard-wrap plain text at terminal cell boundaries.

    Cell-accurate (CJK-aware) via rich's cell arithmetic; never splits a
    double-width character across rows. For unstyled one-off lines — styled
    content goes through :func:`render_ansi_lines` where rich word-wraps.

    Args:
        text: Plain text without escape sequences or newlines.
        width: Maximum cells per line (>= 2).

    Returns:
        Non-empty list of lines, each with ``cell_len <= width``.
    """
    width = max(2, width)
    if not text:
        return [""]
    return chop_cells(text, width)


def render_ansi_lines(renderable: Any, width: int) -> list[str]:
    """Render a rich renderable to ANSI-styled lines of at most ``width`` cells.

    rich does the word-wrapping, wide-character measurement, and per-segment
    styling; each returned line is safe to commit at a cost of exactly one
    screen row. Respects ``NO_COLOR``.

    Args:
        renderable: Any rich renderable (Markdown, Text, str, ...).
        width: Target width in cells.

    Returns:
        Lines without trailing newlines, trailing blank lines dropped.
    """
    console = Console(
        width=max(10, width),
        force_terminal=True,
        legacy_windows=False,
        highlight=False,
        soft_wrap=False,
    )
    with console.capture() as capture:
        console.print(renderable, end="")
    lines = capture.get().split("\n")
    while lines and not strip_ansi(lines[-1]).strip():
        lines.pop()
    return lines


# --------------------------------------------------------------------------
# Newline-gated block splitting (soft-import the shared shaping helpers)
# --------------------------------------------------------------------------


def _fallback_split(buffer: str) -> tuple[list[str], str]:
    """Split committed markdown blocks from a live tail at blank lines.

    Minimal stand-in for the shared renderer's ``split_complete_blocks`` so
    the spike stays runnable standalone. Fence-unaware; good enough for the
    scripted demo content.

    Args:
        buffer: The accumulated stream text.

    Returns:
        ``(blocks, tail)``.
    """
    blocks: list[str] = []
    while "\n\n" in buffer:
        head, buffer = buffer.split("\n\n", 1)
        blocks.append(head + "\n\n")
    return blocks, buffer


try:  # the real splitter understands fences, tables, and list runs
    from chimera.tui.markdown_stream import split_complete_blocks as _split_blocks
except Exception:  # pragma: no cover - standalone fallback
    _split_blocks = _fallback_split


# --------------------------------------------------------------------------
# Runtime (the only code that touches the TTY)
# --------------------------------------------------------------------------


class HybridScreen:
    """Owns the terminal for one hybrid session.

    Everything stateful lives here: geometry, band position, restoration
    hooks (atexit + SIGTERM + sys.excepthook), SIGWINCH handling, and the
    cbreak stdin used for the cursor-position query and composer echo.

    Args:
        out: Text stream to the terminal (stdout).
        band_height: Rows reserved at the bottom.
        assume_bottom: Skip the CPR query and glue the band to the bottom.
    """

    def __init__(
        self,
        out: IO[str],
        band_height: int = 3,
        assume_bottom: bool = False,
        debug_log: str | None = None,
    ) -> None:
        self.out = out
        self.band_height_pref = band_height
        self.assume_bottom = assume_bottom
        self.geom = Geometry.fit(*self._probe_size(), band_height)
        self.band_top = self.geom.band_top
        self.committed = 0
        self._resized = False
        self._restored = False
        self._old_termios: object | None = None
        self._old_excepthook = sys.excepthook
        self._old_sigterm: object | None = None
        self._old_sigwinch: object | None = None
        self._stdin_fd: int | None = None
        self._debug_log = debug_log

    def _log(self, event: str) -> None:
        """Append a state-transition line to the debug log, if enabled.

        Args:
            event: Short label for what just happened.
        """
        if not self._debug_log:
            return
        with contextlib.suppress(OSError), open(self._debug_log, "a", encoding="utf-8") as fh:
            fh.write(
                f"{event} band_top={self.band_top} committed={self.committed} "
                f"geom={self.geom.cols}x{self.geom.rows}\n"
            )

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Enter the hybrid: cbreak stdin, find the cursor, place the band."""
        self._enter_cbreak()
        atexit.register(self._emergency_restore)
        sys.excepthook = self._crash_hook
        self._old_sigterm = signal.signal(signal.SIGTERM, self._on_sigterm)
        if hasattr(signal, "SIGWINCH"):
            self._old_sigwinch = signal.signal(signal.SIGWINCH, self._on_sigwinch)

        cursor_row = self.geom.rows if self.assume_bottom else self._query_cursor_row()
        seq, self.band_top = initial_band_position(
            cursor_row, self.geom.rows, self.geom.band_height
        )
        self._write(seq)
        self._log(f"start cpr_row={cursor_row}")

    def stop(self) -> None:
        """Leave cleanly: erase the band, restore modes, keep the transcript."""
        if self._restored:
            return
        self._restored = True
        self._write(exit_seq(self.band_top))
        self._teardown_hooks()

    def _teardown_hooks(self) -> None:
        """Put back termios, signal handlers, and the excepthook."""
        self._leave_cbreak()
        sys.excepthook = self._old_excepthook
        if self._old_sigterm is not None:
            signal.signal(signal.SIGTERM, self._old_sigterm)  # type: ignore[arg-type]
        if self._old_sigwinch is not None and hasattr(signal, "SIGWINCH"):
            signal.signal(signal.SIGWINCH, self._old_sigwinch)  # type: ignore[arg-type]
        with contextlib.suppress(Exception):
            atexit.unregister(self._emergency_restore)

    def _emergency_restore(self) -> None:
        """Last-resort terminal restoration (atexit / crash / SIGTERM)."""
        if self._restored:
            return
        self._restored = True
        with contextlib.suppress(Exception):
            self._write(emergency_restore_seq() + cup(self.geom.rows, 1) + "\r\n")
        self._leave_cbreak()

    def _crash_hook(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        """Restore the terminal *before* the interpreter prints a traceback."""
        self._emergency_restore()
        self._old_excepthook(exc_type, exc, tb)

    def _on_sigterm(self, signum: int, frame: FrameType | None) -> None:
        """Graceful SIGTERM: restore, then die with the conventional code."""
        self._emergency_restore()
        raise SystemExit(128 + signum)

    def _on_sigwinch(self, signum: int, frame: FrameType | None) -> None:
        """Note a resize; handled on the next tick (handlers must stay tiny)."""
        self._resized = True

    # -- terminal plumbing ---------------------------------------------------

    def _write(self, seq: str) -> None:
        if seq:
            self.out.write(seq)
            self.out.flush()

    def _probe_size(self) -> tuple[int, int]:
        """Return (rows, cols), falling back to 24x80 for pipes and bare ptys.

        A pty with no terminal emulator behind it (``script``, some CI ptys)
        reports a 0x0 winsize — treat that as unknown, not as truth.
        """
        try:
            size = os.get_terminal_size(self.out.fileno())
        except (OSError, ValueError):
            return 24, 80
        if size.lines < 3 or size.columns < 10:
            return 24, 80
        return size.lines, size.columns

    def _enter_cbreak(self) -> None:
        """Cbreak (not raw): per-byte reads, no echo, ISIG intact (Ctrl+C works)."""
        if not sys.stdin.isatty():
            return
        try:
            import termios
            import tty
        except ImportError:  # pragma: no cover - non-POSIX
            return
        fd = sys.stdin.fileno()
        self._stdin_fd = fd
        self._old_termios = termios.tcgetattr(fd)
        tty.setcbreak(fd)

    def _leave_cbreak(self) -> None:
        if self._old_termios is None or self._stdin_fd is None:
            return
        with contextlib.suppress(Exception):
            import termios

            termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._old_termios)
        self._old_termios = None

    def _query_cursor_row(self) -> int:
        """Ask the terminal where the shell left the cursor (DSR 6 / CPR).

        Falls back to the screen bottom when stdin is not a TTY or nothing
        answers within 250 ms (e.g. a bare pty with no emulator behind it).

        Returns:
            1-based cursor row.
        """
        if self._stdin_fd is None:
            return self.geom.rows
        self._write(CPR_QUERY)
        deadline = time.monotonic() + 0.25
        buf = b""
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self._stdin_fd], [], [], deadline - time.monotonic())
            if not ready:
                break
            buf += os.read(self._stdin_fd, 64)
            match = _CPR_RE.search(buf.decode("utf-8", "replace"))
            if match:
                return max(1, min(int(match.group(1)), self.geom.rows))
        return self.geom.rows

    def read_keys(self) -> str:
        """Drain pending stdin bytes (cbreak) into printable characters.

        Escape-prefixed chunks (arrow keys, stray CPR replies) are dropped
        whole — the fake composer only needs plain text and backspace.

        Returns:
            The printable characters read, with ``\\x7f`` kept for backspace
            handling; empty string when stdin is quiet or not a TTY.
        """
        if self._stdin_fd is None:
            return ""
        ready, _, _ = select.select([self._stdin_fd], [], [], 0)
        if not ready:
            return ""
        chunk = os.read(self._stdin_fd, 256)
        if b"\x1b" in chunk:
            return ""
        return chunk.decode("utf-8", "replace")

    # -- the three verbs -----------------------------------------------------

    def commit(self, lines: Sequence[str]) -> None:
        """Push finished lines above the band into native scrollback.

        Mirrors R1's insert: when the band is mid-screen, first reverse-index
        it toward the bottom (freeing blank rows), then write into the region
        above starting from the last committed row.

        Args:
            lines: Pre-wrapped lines (each ``visible_cells() <= geom.cols``).
        """
        if not lines:
            return
        old_top = self.band_top
        room_seq, self.band_top = make_room(
            old_top, self.geom.band_height, self.geom.rows, len(lines)
        )
        seq = commit_lines(
            lines,
            history_bottom=self.band_top - 1,
            start_row=max(1, old_top - 1),
        )
        self._write(room_seq + seq)
        self.committed += len(lines)
        if room_seq:
            self._log(f"make_room from={old_top}")
        self._log(f"commit n={len(lines)}")

    def paint_band(self, band_lines: Sequence[str], park_col: int = 1) -> None:
        """Repaint the band rows in place, parking the cursor in the composer.

        Args:
            band_lines: Pre-fit lines, at most ``geom.band_height`` of them.
            park_col: 1-based column for the visible cursor on the composer
                row (row 2 of the band, or row 1 for a 1-row band).
        """
        rows = list(band_lines)[: self.geom.band_height]
        park_row = self.band_top + (1 if self.geom.band_height >= 2 else 0)
        park_col = max(1, min(park_col, self.geom.cols))
        self._write(band_paint(rows, self.band_top, (park_row, park_col)))

    def handle_resize(self) -> bool:
        """Apply a pending SIGWINCH: recompute geometry, re-glue the band.

        Returns:
            True when a resize was applied (the caller repaints the band).
        """
        if not self._resized:
            return False
        self._resized = False
        self.geom = Geometry.fit(*self._probe_size(), self.band_height_pref)
        seq, self.band_top = resize_reglue(min(self.band_top, self.geom.rows), self.geom)
        self._write(seq)
        self._log("resize")
        return True


# --------------------------------------------------------------------------
# The scripted fake agent stream
# --------------------------------------------------------------------------

_PROSE_1 = (
    "### Native-scrollback hybrid\n\n"
    "Finished lines live in the **terminal's own scrollback** — select and "
    "copy them with the mouse, scroll with the wheel, keep them after exit. "
    "Only the band below repaints; committed rows are written exactly once.\n\n"
)
_PROSE_2 = (
    "The commit batch is a scroll-region operation: `ESC[1;Nr` confines "
    "scrolling to the history area, a linefeed on its bottom row evicts the "
    "top row into scrollback, and `ESC[r` hands the terminal back. Details in "
    "docs/specs/tui-scrollback-hybrid.md and the reference-corpus dossier.\n\n"
)
_CODE_BLOCK = (
    "```python\n"
    "def commit(lines: list[str]) -> None:\n"
    '    """Push finished lines above the band."""\n'
    "    write(region_setup(1, band_top - 1))\n"
    "    for line in lines:\n"
    '        write("\\r\\n" + line)  # scrolls at the bottom margin\n'
    "    write(region_reset())\n"
    "```\n\n"
)
_PROSE_WIDE = (
    "宽字符也按两个单元格换行：终端滚动区域混合渲染，提交行进入原生回滚缓冲区，"
    "底部面板原地重绘。Long URLs fold at cell boundaries: "
    "https://example.invalid/spikes/scrollback-hybrid/a-rather-long-path/segment/42\n\n"
)
_TOOL_LINES = (
    "✓ read(chimera/tui/render.py) · 0.1s",
    "✓ bash(uv run pytest tests/spikes -q) · 3.1s",
    "✓ edit(chimera/tui/multiplex.py) · +12 -3",
)


def scripted_stream() -> Iterator[tuple[str, str]]:
    """Yield the scripted fake agent events, forever (cycled by the caller).

    Yields:
        ``("delta", chunk)`` for streaming markdown text and
        ``("tool", label)`` for one-line tool results.
    """
    sections = [_PROSE_1, _PROSE_2, _CODE_BLOCK, _PROSE_WIDE]
    while True:
        for i, section in enumerate(sections):
            for chunk in re.findall(r"\S+\s*", section):
                yield "delta", chunk
            yield "tool", _TOOL_LINES[i % len(_TOOL_LINES)]


def _tool_line(label: str, width: int) -> list[str]:
    """Render a tool-result line dim-green, pre-fit to ``width``."""
    return render_ansi_lines(Text(label, style="green3"), width)


def _band_rows(
    screen: HybridScreen,
    composer_text: str,
    spinner_frame: str,
    elapsed: float,
    phase: str,
    tail_words: int,
) -> tuple[list[str], int]:
    """Build the band's rows (separator, composer, status) at current width.

    Args:
        screen: The live screen (geometry + committed count).
        composer_text: What the fake composer currently holds.
        spinner_frame: Current spinner glyph.
        elapsed: Seconds since the stream started.
        phase: Short state label.
        tail_words: Words waiting in the live (uncommitted) tail.

    Returns:
        ``(rows, park_col)`` — the rows pre-fit to the width, and the 1-based
        column where the visible cursor parks on the composer row.
    """
    width = screen.geom.cols
    title = " native scrollback ↑ "
    rule = "─" * max(0, width - visible_cells(title) - 2)
    separator = render_ansi_lines(Text(f"╌{title}{rule}", style="dim"), width)[0]

    prompt = "❯ "
    keep = max(4, width - len(prompt) - 2)
    typed = composer_text[-keep:]
    composer_src = Text(prompt, style="bold cyan")
    composer_src.append(typed)
    composer = render_ansi_lines(composer_src, width)[0]
    park_col = min(width, len(prompt) + cell_len(typed) + 1)

    status_src = Text()
    status_src.append(f"{spinner_frame} {phase}", style="yellow")
    status_src.append(
        f" · {elapsed:5.1f}s · {screen.committed} lines committed"
        f" · tail {tail_words}w · {width}×{screen.geom.rows}",
        style="dim",
    )
    if sys.stdin.isatty():
        status_src.append(" · type to echo, q quits", style="dim")
    status = render_ansi_lines(status_src, width)[0]

    rows = [separator, composer, status]
    return rows, park_col


def _play(screen: HybridScreen, args: argparse.Namespace) -> None:
    """Drive the scripted stream against a started :class:`HybridScreen`.

    Args:
        screen: A screen on which :meth:`HybridScreen.start` already ran.
        args: Parsed CLI arguments.

    Raises:
        RuntimeError: When ``--crash-at`` fires (deliberate, uncaught).
        KeyboardInterrupt: When the user quits with ``q`` or Ctrl+C.
    """
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    delay = max(0.0, args.delay / 1000.0)
    started = time.monotonic()
    buffer = ""
    composer_text = ""
    ticks = 0
    last_paint = 0.0
    stream = scripted_stream()

    while screen.committed < args.rows:
        kind, payload = next(stream)
        committed_now = False
        if kind == "delta":
            buffer += payload
            blocks, buffer = _split_blocks(buffer)
            for block in blocks:
                screen.commit(render_ansi_lines(Markdown(block), screen.geom.cols))
                committed_now = True
                if args.crash_at and screen.committed >= args.crash_at:
                    raise RuntimeError(
                        f"deliberate crash after {screen.committed} committed lines"
                    )
        else:
            screen.commit(_tool_line(payload, screen.geom.cols))
            committed_now = True

        for ch in screen.read_keys():
            if ch in ("q", "\x03"):
                raise KeyboardInterrupt
            if ch == "\x7f":
                composer_text = composer_text[:-1]
            elif ch.isprintable():
                composer_text += ch

        resized = screen.handle_resize()
        now = time.monotonic()
        # A commit may have moved the band (make-room) — repaint right away so
        # the parked cursor and chrome are never stale for a visible interval.
        if resized or committed_now or now - last_paint >= 0.05:
            ticks += 1
            rows, park_col = _band_rows(
                screen,
                composer_text,
                spinner[ticks % len(spinner)],
                now - started,
                "streaming",
                len(buffer.split()),
            )
            screen.paint_band(rows, park_col)
            last_paint = now
        if delay:
            time.sleep(delay)

    if buffer.strip():
        screen.commit(render_ansi_lines(Markdown(buffer), screen.geom.cols))
    rows, park_col = _band_rows(screen, composer_text, "✓", time.monotonic() - started, "done", 0)
    screen.paint_band(rows, park_col)
    if args.linger:
        end = time.monotonic() + args.linger / 1000.0
        while time.monotonic() < end:
            if "q" in screen.read_keys():
                break
            if screen.handle_resize():
                rows, park_col = _band_rows(
                    screen, composer_text, "✓", time.monotonic() - started, "done", 0
                )
                screen.paint_band(rows, park_col)
            time.sleep(0.05)


def run_demo(args: argparse.Namespace) -> int:
    """Play the scripted stream through a :class:`HybridScreen`.

    With ``--crash-at`` the play loop is deliberately NOT wrapped in
    try/finally: the RuntimeError must escape with no clean shutdown so the
    run proves the emergency paths (sys.excepthook restores the terminal
    before the traceback prints; atexit is the backstop).

    Args:
        args: Parsed CLI arguments.

    Returns:
        Process exit code.
    """
    out = sys.stdout
    screen = HybridScreen(
        out,
        band_height=args.band_height,
        assume_bottom=args.assume_bottom,
        debug_log=args.debug_log,
    )
    screen.start()
    if args.crash_at:
        _play(screen, args)  # no finally, by design — see docstring
        screen.stop()
    else:
        try:
            _play(screen, args)
        except KeyboardInterrupt:
            pass
        finally:
            screen.stop()
    out.write(f"transcript above · {screen.committed} lines committed to native scrollback\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Spike: DECSTBM native-scrollback hybrid transcript renderer."
    )
    parser.add_argument("--rows", type=int, default=40, help="committed-line target (default 40)")
    parser.add_argument(
        "--delay", type=float, default=15.0, help="inter-chunk delay in ms (default 15)"
    )
    parser.add_argument(
        "--band-height", type=int, default=3, help="bottom band rows (default 3)"
    )
    parser.add_argument(
        "--crash-at",
        type=int,
        default=0,
        help="raise RuntimeError after N committed lines (proves crash restoration)",
    )
    parser.add_argument(
        "--assume-bottom",
        action="store_true",
        help="skip the cursor-position query; glue the band to the screen bottom",
    )
    parser.add_argument(
        "--linger",
        type=float,
        default=1200.0,
        help="ms to keep the band up after the stream ends (default 1200; 0 disables)",
    )
    parser.add_argument(
        "--debug-log",
        default=None,
        help="append band-position transitions to this file (for headless verification)",
    )
    args = parser.parse_args(argv)
    return run_demo(args)


if __name__ == "__main__":
    sys.exit(main())

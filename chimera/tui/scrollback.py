"""Native-scrollback hybrid: escape-sequence builders + the terminal runtime.

This is the productized version of the proven spike
``scripts/spikes/scrollback_hybrid.py`` (spike report:
``docs/specs/tui-scrollback-hybrid.md``, verdict **GO — conditional**). The
spike script and its byte-exact tests are kept as the lineage record; this
module lifts the pure builders and the :class:`HybridScreen` runtime into the
package, adds terminal-capability detection (the mandatory multiplexer /
POSIX / TTY gate), and is what the opt-in inline daily driver drives
(:mod:`chimera.tui.inline_frontend`, behind ``chimera code --tui --inline``).

The mechanism, in one paragraph: finished transcript lines are written *once*
into the terminal's normal buffer and scrolled up into the terminal's own
**native scrollback** — where mouse selection, copy, wheel-scroll, and
after-exit persistence all keep working because the terminal owns those rows —
while a reserved **bottom band** (separator / composer / status) repaints in
place with plain cursor addressing and never scrolls. There is no alternate
screen and no mouse capture: the two choices that make native selection
possible are the *absence* of two escape sequences. Committing is a DECSTBM
scroll-region operation: confine scrolling to the history area above the band,
park the cursor on the region's bottom row, and each ``\\r\\n`` there evicts the
top screen row into scrollback.

**Zero new dependencies:** stdlib only. The escape builders are pure functions
(the byte sequences are pinned by :mod:`tests.tui.test_scrollback`); only
:class:`HybridScreen` touches the TTY (termios/tty are imported lazily inside
its methods so this module imports cleanly on non-POSIX platforms). Rich-based
line rendering lives in :mod:`chimera.tui.inline_frontend`, not here — this
module never measures or renders content, it only moves pre-fit lines.

**Scope (v1, per the spike's GO conditions):** POSIX only; the one proven hard
failure — a terminal multiplexer that drops rows evicted from a *partial*
scroll region from its scrollback — is detected by its session env var
(:data:`SCROLLBACK_HOSTILE_ENV_VARS`) and refused so scrollback is never
silently lost. See :func:`inline_capability`.
"""
from __future__ import annotations

import atexit
import contextlib
import os
import re
import select
import signal
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import FrameType, TracebackType
from typing import IO

__all__ = [
    "SCROLLBACK_HOSTILE_ENV_VARS",
    "Geometry",
    "HybridScreen",
    "InlineDecision",
    "band_paint",
    "commit_lines",
    "cup",
    "emergency_restore_seq",
    "exit_seq",
    "initial_band_position",
    "inline_capability",
    "make_room",
    "region_reset",
    "region_setup",
    "resize_reglue",
    "strip_ansi",
]

# --------------------------------------------------------------------------
# Escape-sequence vocabulary (see docs/specs/tui-scrollback-hybrid.md §2)
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

#: Session environment variables whose presence means the host multiplexer
#: drops rows evicted from a *partial* scroll region from its scrollback — the
#: one hard failure the spike proved (report §3/§5): committed lines beyond one
#: screenful are lost, so the hybrid must refuse or fall back there rather than
#: silently lose transcript. Named by env var, not editorialized. Any unprobed
#: multiplexer that shares this eviction behavior can be added as a data row.
SCROLLBACK_HOSTILE_ENV_VARS: tuple[str, ...] = ("ZELLIJ",)


def strip_ansi(text: str) -> str:
    """Remove the escape sequences this module emits from ``text``.

    Args:
        text: A string possibly containing CSI/OSC/ESC sequences.

    Returns:
        The visible characters only.
    """
    return _ANSI_RE.sub("", text)


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

    The maneuver for a band that is not yet glued to the screen bottom (the
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
    band is repositioned at the new bottom. (Re-deriving wrapped history from
    source on resize is a filed follow-up, out of scope for v1.)

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


# --------------------------------------------------------------------------
# Capability detection (the mandatory gate — spike §5/§6)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class InlineDecision:
    """Result of :func:`inline_capability`.

    Attributes:
        use_inline: True only when the native-scrollback hybrid is safe to run.
        reason: A short, stable, brand-free token explaining the decision —
            ``"inline"`` when on; ``"disabled"`` (not requested),
            ``"non-posix"``, ``"not-a-tty"``, or ``"multiplexer:<ENV_VAR>"``
            when off. The multiplexer case names the detected session env var,
            never editorialized, so a caller can log/report it verbatim.
    """

    use_inline: bool
    reason: str

    @property
    def refused(self) -> bool:
        """True when inline was *requested* but cannot run (not merely off).

        The caller uses this to decide whether to print the fall-back-to-
        full-screen note: a plain ``"disabled"`` decision is silent.
        """
        return not self.use_inline and self.reason != "disabled"


def inline_capability(
    requested: bool,
    *,
    platform: str | None = None,
    stdout_isatty: bool | None = None,
    stdin_isatty: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> InlineDecision:
    """Decide whether the native-scrollback inline mode may run (pure).

    Enforces the spike's GO conditions in order: opt-in, POSIX-only, a real
    interactive terminal on *both* streams (the hybrid queries the cursor and
    reads keys from stdin, and paints escape sequences to stdout), and no host
    multiplexer known to drop partial-scroll-region evictions from its
    scrollback. On any failure inline is refused and the caller falls back to
    the full-screen frontend — scrollback is never silently lost.

    All inputs are injectable so the decision is exhaustively testable without
    a terminal; each defaults to the live process value.

    Args:
        requested: Whether inline was asked for (CLI flag or ``tui.inline``).
        platform: ``sys.platform`` value (``"win32"`` → refused as non-POSIX).
        stdout_isatty: Whether stdout is a TTY (defaults to the live check).
        stdin_isatty: Whether stdin is a TTY (defaults to the live check).
        env: Environment mapping to probe for the hostile-multiplexer vars.

    Returns:
        An :class:`InlineDecision`.
    """
    if not requested:
        return InlineDecision(False, "disabled")
    plat = platform if platform is not None else sys.platform
    if plat.startswith("win") or os.name == "nt":
        return InlineDecision(False, "non-posix")
    out_tty = stdout_isatty if stdout_isatty is not None else sys.stdout.isatty()
    in_tty = stdin_isatty if stdin_isatty is not None else sys.stdin.isatty()
    if not out_tty or not in_tty:
        return InlineDecision(False, "not-a-tty")
    environ: Mapping[str, str] = env if env is not None else os.environ
    for var in SCROLLBACK_HOSTILE_ENV_VARS:
        if environ.get(var):
            return InlineDecision(False, f"multiplexer:{var}")
    return InlineDecision(True, "inline")


# --------------------------------------------------------------------------
# Runtime (the only code that touches the TTY)
# --------------------------------------------------------------------------


class HybridScreen:
    """Owns the terminal for one native-scrollback session.

    Everything stateful lives here: geometry, band position, restoration hooks
    (atexit + SIGTERM + ``sys.excepthook``), SIGWINCH handling, and the cbreak
    stdin used for the startup cursor-position query and composer input. The
    escape math is delegated to the module-level pure builders; this class is
    the side-effecting shell around them.

    State-restoration guarantees (all proven by the spike, carried here with
    tests): entering leaves the shell's prior output untouched; a clean
    :meth:`stop` erases only the band chrome and restores modes; a crash
    restores the terminal *before* the traceback prints (via ``excepthook``,
    with SIGTERM + atexit as backstops) and is non-destructive so the
    traceback and transcript stay readable; a resize re-glues the band to the
    new bottom on the next :meth:`handle_resize`.

    Args:
        out: Text stream to the terminal (usually ``sys.stdout``).
        band_height: Rows reserved at the bottom for the band.
        assume_bottom: Skip the startup cursor-position query and glue the
            band straight to the screen bottom (used by tests and pipes).
    """

    def __init__(
        self,
        out: IO[str],
        band_height: int = 3,
        assume_bottom: bool = False,
    ) -> None:
        self.out = out
        self.band_height_pref = band_height
        self.assume_bottom = assume_bottom
        self.geom = Geometry.fit(*self._probe_size(), band_height)
        self.band_top = self.geom.band_top
        self.committed = 0
        self._resized = False
        self._restored = False
        self._started = False
        self._old_termios: object | None = None
        self._old_excepthook = sys.excepthook
        self._old_sigterm: object | None = None
        self._old_sigwinch: object | None = None
        self._stdin_fd: int | None = None

    # -- introspection ------------------------------------------------------

    @property
    def stdin_fd(self) -> int | None:
        """The cbreak stdin file descriptor, or ``None`` when stdin is no TTY.

        The inline frontend registers this with the event loop
        (``loop.add_reader``) to read composer keystrokes; ``None`` means there
        is no interactive stdin (the frontend then only streams, no input).
        """
        return self._stdin_fd

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Enter the hybrid: cbreak stdin, find the cursor, place the band."""
        self._enter_cbreak()
        atexit.register(self._emergency_restore)
        sys.excepthook = self._crash_hook
        self._old_sigterm = signal.signal(signal.SIGTERM, self._on_sigterm)
        if hasattr(signal, "SIGWINCH"):
            self._old_sigwinch = signal.signal(signal.SIGWINCH, self._on_sigwinch)
        self._started = True
        self._restored = False

        cursor_row = self.geom.rows if self.assume_bottom else self._query_cursor_row()
        seq, self.band_top = initial_band_position(
            cursor_row, self.geom.rows, self.geom.band_height
        )
        self._write(seq)

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
        except (OSError, ValueError, AttributeError):
            return 24, 80
        if size.lines < 3 or size.columns < 10:
            return 24, 80
        return size.lines, size.columns

    def _enter_cbreak(self) -> None:
        """Cbreak (not raw): per-byte reads, no echo, ISIG intact so the
        terminal's own signal keys keep working alongside our own handling."""
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

            # _old_termios is the opaque attr list from tcgetattr, stored as
            # object so this module imports on non-POSIX; hand it straight back.
            termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._old_termios)  # type: ignore[arg-type]
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

    def read_available(self, limit: int = 1024) -> bytes:
        """Read whatever cbreak stdin has ready right now; ``b""`` otherwise.

        Intended to be called when the descriptor is already known readable
        (an event loop's ``add_reader`` callback), so this does a single
        non-blocking read and never waits. Returns raw bytes — the caller
        decodes (UTF-8 may split across reads) and routes control keys.

        Args:
            limit: Maximum bytes to read in one call.

        Returns:
            The bytes read, or ``b""`` when there is no interactive stdin or
            nothing is available.
        """
        if self._stdin_fd is None:
            return b""
        ready, _, _ = select.select([self._stdin_fd], [], [], 0)
        if not ready:
            return b""
        with contextlib.suppress(OSError):
            return os.read(self._stdin_fd, limit)
        return b""

    # -- the three verbs -----------------------------------------------------

    def commit(self, lines: Sequence[str]) -> None:
        """Push finished lines above the band into native scrollback.

        When the band is mid-screen, first reverse-index it toward the bottom
        (freeing blank rows), then write into the region above starting from
        the last committed row.

        Args:
            lines: Pre-wrapped lines (each visible width <= ``geom.cols``).
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
        return True

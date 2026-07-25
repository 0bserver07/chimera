"""The opt-in inline (native-scrollback) daily-driver frontend.

This is the runtime that drives an :class:`~chimera.assembly.driver.AgentDriver`
through the native-scrollback hybrid (:mod:`chimera.tui.scrollback`): committed
transcript lines flow up into the terminal's own scrollback while a pinned
bottom band holds the composer and status line. It is reached only from
:func:`chimera.tui.multiplex.run_single_agent` when inline is requested *and*
:func:`chimera.tui.scrollback.inline_capability` clears it (opt-in, POSIX,
interactive TTY, no scrollback-hostile multiplexer). Default OFF; the
full-screen frontend is unchanged.

Split of concerns (why this module and not ``scrollback.py``): ``scrollback``
owns escape sequences and the terminal and is stdlib-only; *this* module owns
rich-based line rendering and the async input/stream loop. It reuses the shared
transcript renderer (:class:`chimera.tui.render.LaneTranscript`, and through it
:func:`chimera.tui.markdown_stream.split_complete_blocks`), so committed prose
looks identical to the full-screen frontend and to the persisted transcript.

Testability: the width helpers, the pure key interpreter
(:func:`interpret_key`), and the pure band builder (:func:`build_band_rows`)
are unit-tested TTY-free; only :class:`InlineFrontend` touches the loop and the
screen. Rich is an optional extra, so tests importing this module use
``pytest.importorskip``.

Known v1 limitations (documented, not bugs — see
``docs/guides/inline-mode.md``): the composer is inert mid-turn (Ctrl+C
cancels; type again after), the live streaming tail is summarized in the status
line rather than previewed above the band (so the layout never jumps), and
resize cannot reflow rows already committed to the terminal's scrollback.
"""
from __future__ import annotations

import asyncio
import codecs
import contextlib
import signal
import sys
import time
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Any

from rich.cells import cell_len, chop_cells
from rich.console import Console
from rich.text import Text

from chimera.tui.render import LaneTranscript
from chimera.tui.scrollback import HybridScreen, strip_ansi
from chimera.tui.shell_marks import ShellMarks

if TYPE_CHECKING:
    from chimera.tui.lane import Lane

__all__ = [
    "BandModel",
    "InlineFrontend",
    "KeyAction",
    "build_band_rows",
    "hard_wrap_cells",
    "interpret_key",
    "render_ansi_lines",
    "run_inline",
    "visible_cells",
]

#: Spinner glyphs for the working status row (Braille dots — one cell each).
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_HELP_LINES: tuple[str, ...] = (
    "inline mode — native terminal scrollback; the composer + status pin below.",
    "  /help /clear /cost /model /tools /exit    Ctrl+C cancels a turn (or quits when idle)",
    "  select & copy with the mouse · wheel-scroll into history · transcript persists after exit",
)


# --------------------------------------------------------------------------
# Rich line rendering (width-accurate; lifted from the spike)
# --------------------------------------------------------------------------


def visible_cells(text: str) -> int:
    """Return the terminal cell width of ``text`` ignoring escape sequences."""
    return int(cell_len(strip_ansi(text)))


def hard_wrap_cells(text: str, width: int) -> list[str]:
    """Hard-wrap plain text at terminal cell boundaries (CJK-aware).

    Never splits a double-width character across rows. For unstyled one-off
    lines; styled content goes through :func:`render_ansi_lines`.

    Args:
        text: Plain text without escape sequences or newlines.
        width: Maximum cells per line (>= 2).

    Returns:
        Non-empty list of lines, each with ``cell_len <= width``.
    """
    width = max(2, width)
    if not text:
        return [""]
    return list(chop_cells(text, width))


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
    # Annotated so mypy keeps a concrete list[str] under CI's no-rich posture
    # (rich resolves to Any there — a bare inference would return Any).
    lines: list[str] = capture.get().split("\n")
    while lines and not strip_ansi(lines[-1]).strip():
        lines.pop()
    return lines


# --------------------------------------------------------------------------
# Pure keyboard interpretation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyAction:
    """The effect of one keystroke on the composer (pure result).

    Attributes:
        composer: The composer text after the keystroke.
        submit: Text to submit when Enter was pressed, else ``None``. May be
            empty (the caller skips empty submissions).
        quit: Whether the keystroke asked to leave (Ctrl+D on an empty line).
    """

    composer: str
    submit: str | None = None
    quit: bool = False


def interpret_key(ch: str, composer: str, *, running: bool) -> KeyAction:
    """Map one decoded key to its effect on the composer (pure).

    Ctrl+C is intentionally *not* handled here — cbreak leaves ISIG enabled, so
    it arrives as SIGINT and :class:`InlineFrontend` handles it via a signal
    handler (cancel a turn, or quit when idle). The composer is inert while a
    turn streams (v1: no mid-turn steering); Enter and printable keys are
    ignored until the turn ends.

    Args:
        ch: One decoded character from stdin.
        composer: The current composer text.
        running: Whether a turn is currently streaming.

    Returns:
        The resulting :class:`KeyAction`.
    """
    if ch in ("\r", "\n"):
        if running:
            return KeyAction(composer)
        return KeyAction("", submit=composer)
    if ch in ("\x7f", "\x08"):  # DEL / Backspace
        return KeyAction(composer[:-1])
    if ch == "\x15":  # Ctrl+U — kill line
        return KeyAction("")
    if ch == "\x04":  # Ctrl+D — quit on an empty idle line
        if not running and not composer:
            return KeyAction(composer, quit=True)
        return KeyAction(composer)
    if running:
        return KeyAction(composer)
    if ch.isprintable() and ch != "\x00":
        return KeyAction(composer + ch)
    return KeyAction(composer)


# --------------------------------------------------------------------------
# Pure band builder (separator / composer / status)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BandModel:
    """Everything the band renders, snapshotted (pure input to the builder)."""

    model: str
    composer: str
    running: bool
    cost: float
    steps: int
    elapsed: float
    committed: int
    thinking: bool
    thinking_chars: int
    tail_chars: int
    rows_total: int
    frame: int
    interactive: bool
    #: R-THEME-4 motion gate: False freezes the spinner to a static glyph.
    animate: bool = True


def build_band_rows(m: BandModel, cols: int) -> tuple[list[str], int]:
    """Build the pinned band's rows and the composer cursor column (pure).

    Three rows: a dim separator advertising native scrollback, the composer
    line, and a status line (a working spinner + elapsed + cost + steps + a
    thinking/​tail hint while a turn streams; an idle summary otherwise). Every
    row is pre-fit to ``cols`` cells so each costs exactly one screen row.

    Args:
        m: The band state snapshot.
        cols: Screen width in cells.

    Returns:
        ``(rows, park_col)`` — the three pre-fit row strings and the 1-based
        column where the visible cursor parks on the composer row.
    """
    title = " native scrollback ↑ "
    rule = "─" * max(0, cols - visible_cells(title) - 2)
    separator = _fit(Text(f"╌{title}{rule}", style="dim"), cols)

    prompt = "❯ "
    keep = max(4, cols - len(prompt) - 2)
    typed = m.composer[-keep:]
    composer_src = Text(prompt, style="bold cyan")
    composer_src.append(typed)
    composer = _fit(composer_src, cols)
    park_col = min(cols, len(prompt) + cell_len(typed) + 1)

    status_src = Text()
    if m.running:
        spin = _SPINNER[m.frame % len(_SPINNER)] if m.animate else _SPINNER[0]
        status_src.append(f"{spin} working", style="yellow")
        status_src.append(
            f" · {m.elapsed:4.1f}s · ${m.cost:.4f} · {m.steps} steps", style="dim"
        )
        if m.thinking:
            status_src.append(f" · ∴ thinking {_kchars(m.thinking_chars)}", style="dim")
        elif m.tail_chars:
            status_src.append(f" · writing {_kchars(m.tail_chars)}", style="dim")
    else:
        status_src.append("● ready", style="green")
        status_src.append(
            f" · {m.model} · ${m.cost:.4f} · {m.steps} steps"
            f" · {m.committed} lines · {cols}×{m.rows_total}",
            style="dim",
        )
        if m.interactive:
            status_src.append(" · Ctrl+C quits", style="dim")
    status = _fit(status_src, cols)

    return [separator, composer, status], park_col


def _fit(renderable: Any, cols: int) -> str:
    """Render one rich renderable to a single ``cols``-cell line."""
    lines = render_ansi_lines(renderable, cols)
    return lines[0] if lines else ""


def _kchars(n: int) -> str:
    """Compact character-count label (honest size, not tokens)."""
    return f"{n} chars" if n < 1000 else f"~{n / 1000:.1f}k chars"


# --------------------------------------------------------------------------
# The runtime frontend
# --------------------------------------------------------------------------

_QUIT = object()  # sentinel enqueued to leave the input loop


class InlineFrontend:
    """Drive one lane's :class:`AgentDriver` through the native-scrollback hybrid.

    Owns the async loop that interleaves streaming a turn's events (committed to
    scrollback via :class:`LaneTranscript`) with reading composer keystrokes and
    repainting the pinned band. Persistence is identical to the full-screen
    frontend: it folds events into the same :class:`~chimera.tui.lane.Lane`
    (``on_turn_begin`` / ``record`` / ``note`` / ``on_turn_end``), so the cohort
    artifact and resume work unchanged.

    Args:
        lane: The single lane to drive (its ``driver`` and ``config``).
        out: Output stream (defaults to ``sys.stdout``).
        band_height: Rows reserved for the bottom band.
        markdown: Render committed assistant prose as rich Markdown.
        clock: Monotonic time source (injectable for tests).
        palette: Semantic slot colors (R-THEME-1); ``None`` = the default
            theme, whose slots reproduce the pre-theme styles exactly.
        animations: R-THEME-4 motion gate; False freezes the spinner and the
            reasoning heartbeat to static glyphs.
        shell_marks: Emit OSC 133 shell-integration zone marks around each
            committed turn (``[tui] shell_integration``), so the terminal can
            jump prompt-to-prompt through the transcript in its own
            scrollback. Off by default; see :mod:`chimera.tui.shell_marks`.
    """

    def __init__(
        self,
        lane: Lane,
        *,
        out: IO[str] | None = None,
        band_height: int = 3,
        markdown: bool = True,
        clock: Any = None,
        palette: Any = None,
        animations: bool = True,
        shell_marks: bool = False,
    ) -> None:
        self.lane = lane
        self.driver = lane.driver
        self.screen = HybridScreen(out if out is not None else sys.stdout, band_height=band_height)
        self.transcript = LaneTranscript(self._sink, markdown=markdown, palette=palette)
        self._animations = bool(animations)
        #: Pending OSC 133 marks, drained onto the next committed row. Inert
        #: (and byte-identical to before) unless the knob is on.
        self.marks = ShellMarks(enabled=shell_marks)
        self.composer = ""
        self._model = str(getattr(lane.config, "model", "?"))
        self._clock = clock if clock is not None else time.monotonic
        self._running = False
        self._quit = False
        self._frame = 0
        self._turn_order = 0
        self._turn_started: float | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._input_q: asyncio.Queue[Any] = asyncio.Queue()

    # -- transcript sink ----------------------------------------------------

    def _sink(self, renderable: Any) -> None:
        """Commit one rendered transcript item into native scrollback.

        A renderable that renders to nothing (the blank spacer the transcript
        writes between markdown blocks) still commits one blank row, so
        block spacing survives into scrollback.

        Any pending OSC 133 zone mark rides along as the row's prefix — that
        is the only way a mark attaches to a *transcript* row rather than to
        the band the cursor rests in. The marks are zero-width, so nothing
        about the row's width accounting changes.
        """
        lines = render_ansi_lines(renderable, self.screen.geom.cols)
        self.screen.commit(lines if lines else [""], prefix=self.marks.take())

    def _emit(self, text: str, style: str = "dim") -> None:
        """Commit a frontend-originated line (display only, not persisted)."""
        self._sink(Text(text, style=style))

    # -- the loop -----------------------------------------------------------

    async def run(self, initial_task: str | None = None) -> None:
        """Enter the hybrid, drive turns and input until the user leaves.

        Guarantees the terminal is restored on every exit path — clean quit,
        exception, or signal — via :meth:`HybridScreen.stop` in ``finally``
        (with the screen's atexit/excepthook/SIGTERM backstops behind it).

        Args:
            initial_task: An optional first task, auto-submitted on launch.
        """
        self.screen.start()
        self._loop = asyncio.get_running_loop()
        fd = self.screen.stdin_fd
        if fd is not None:
            self._loop.add_reader(fd, self._on_readable)
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            self._loop.add_signal_handler(signal.SIGINT, self._on_interrupt)
        ticker = self._loop.create_task(self._ticker())
        self._intro()
        self._repaint()
        try:
            if initial_task and initial_task.strip():
                await self._run_turn(initial_task.strip())
            while not self._quit:
                self._repaint()
                item = await self._input_q.get()
                if item is _QUIT:
                    break
                text = str(item).strip()
                if not text:
                    continue
                if text.startswith("/"):
                    if self._handle_command(text):
                        break
                else:
                    await self._run_turn(text)
        finally:
            ticker.cancel()
            with contextlib.suppress(BaseException):
                await ticker
            if fd is not None:
                with contextlib.suppress(Exception):
                    self._loop.remove_reader(fd)
            with contextlib.suppress(Exception):
                self._loop.remove_signal_handler(signal.SIGINT)
            self.screen.stop()

    def _intro(self) -> None:
        c = getattr(self.driver, "context_window", None)
        ctx = f"{c:,}" if c else "?"
        self._emit(
            f"Chimera inline — {self._model} — {ctx} ctx.  "
            f"/help · Ctrl+C cancels/quits · native scrollback ↑",
        )

    async def _run_turn(self, text: str) -> None:
        """Stream one turn, committing events to scrollback and persisting them.

        The turn is bracketed by OSC 133 zone marks when shell integration is
        on: prompt-start/input-start ride the echoed prompt row, output-start
        rides the first row the turn produces, and command-end is queued at
        the end for the next committed row (the next turn's prompt row —
        exactly the ``D`` then ``A`` pairing a shell emits at its prompt).
        """
        self._running = True
        self._turn_started = self._clock()
        self.lane.on_turn_begin()
        self.marks.turn_start()
        self._sink(Text.assemble(("› ", "bold cyan"), (text, "bold")))
        self.marks.output_start()
        self.lane.note(f"› {text}")
        self._repaint()
        try:
            async for ev in self.driver.send(text):
                self.lane.record(ev)
                self.transcript.handle(ev)
                self._repaint()
        except Exception as exc:  # noqa: BLE001 - surfaced to the transcript
            self.lane.telemetry.terminal_reason = "error"
            self._emit(f"turn failed: {exc}", style="red")
        finally:
            self.transcript.commit()
            self._turn_order += 1
            self.lane.on_turn_end(order=self._turn_order)
            self.marks.turn_end(ok=self.lane.telemetry.terminal_reason != "error")
            self._running = False
            self._turn_started = None
            self._repaint()

    async def _ticker(self) -> None:
        """Advance the spinner/heartbeat and apply pending resizes (~10 Hz)."""
        while True:
            await asyncio.sleep(0.1)
            self._frame += 1
            resized = self.screen.handle_resize()
            if resized or self._running:
                self._repaint()

    # -- input --------------------------------------------------------------

    def _on_readable(self) -> None:
        """Event-loop callback: drain ready stdin, route keys, repaint."""
        data = self.screen.read_available()
        if not data:
            return
        text = self._decoder.decode(data)
        for ch in text:
            if ch == "\x1b":  # drop the rest of an escape sequence (arrows, etc.)
                break
            self._feed_key(ch)
        self._repaint()

    def _feed_key(self, ch: str) -> None:
        action = interpret_key(ch, self.composer, running=self._running)
        self.composer = action.composer
        if action.quit:
            self._trigger_quit()
        elif action.submit is not None:
            self._input_q.put_nowait(action.submit)

    def _on_interrupt(self) -> None:
        """SIGINT (Ctrl+C): cancel a running turn, or quit when idle."""
        if self._running:
            self.driver.cancel()
            self._emit("· cancel requested (Ctrl+C)", style="red")
            self._repaint()
        else:
            self._trigger_quit()

    def _trigger_quit(self) -> None:
        self._quit = True
        self._input_q.put_nowait(_QUIT)

    # -- rendering ----------------------------------------------------------

    def _elapsed(self) -> float:
        if self._turn_started is None:
            return 0.0
        return self._clock() - self._turn_started

    def _band_model(self) -> BandModel:
        return BandModel(
            model=self._model,
            composer=self.composer,
            running=self._running,
            cost=float(self.lane.telemetry.cost),
            steps=int(self.lane.telemetry.steps),
            elapsed=self._elapsed(),
            committed=self.screen.committed,
            thinking=self.transcript.thinking_active,
            thinking_chars=self.transcript.thinking_chars,
            tail_chars=len(self.transcript.live_tail),
            rows_total=self.screen.geom.rows,
            frame=self._frame,
            interactive=self.screen.stdin_fd is not None,
            animate=self._animations,
        )

    def _repaint(self) -> None:
        rows, park_col = build_band_rows(self._band_model(), self.screen.geom.cols)
        self.screen.paint_band(rows, park_col)

    # -- slash commands (frontend-local, minimal) ---------------------------

    def _handle_command(self, text: str) -> bool:
        """Handle a ``/command``; return True to leave the loop (``/exit``)."""
        cmd = text.split()[0]
        if cmd in ("/exit", "/quit"):
            return True
        if cmd == "/help":
            for line in _HELP_LINES:
                self._emit(line)
        elif cmd == "/clear":
            self.driver.clear()
            self._emit("(conversation cleared)")
        elif cmd == "/cost":
            self._emit(f"cumulative: ${float(self.lane.telemetry.cost):.4f}")
        elif cmd == "/model":
            c = getattr(self.driver, "context_window", None)
            ctx = f"{c:,}" if c else "?"
            self._emit(f"{self._model}  ({ctx} ctx)")
        elif cmd == "/tools":
            names = ", ".join(str(getattr(t, "name", "?")) for t in getattr(self.driver, "tools", []))
            self._emit(names or "(none)")
        else:
            self._emit(f"unknown command: {cmd}", style="red")
        return False


def run_inline(
    lane: Lane,
    *,
    initial_task: str | None = None,
    band_height: int = 3,
    markdown: bool = True,
    out: IO[str] | None = None,
    palette: Any = None,
    animations: bool = True,
    shell_marks: bool = False,
) -> None:
    """Run the inline daily driver for one lane (blocking).

    Wraps :meth:`InlineFrontend.run` in an event loop. The caller
    (:func:`chimera.tui.multiplex.run_single_agent`) has already confirmed the
    inline mode is safe via :func:`chimera.tui.scrollback.inline_capability`,
    and persists/cleans up the cohort afterwards.

    Args:
        lane: The single lane to drive.
        initial_task: Optional first task, auto-submitted on launch.
        band_height: Rows reserved for the bottom band.
        markdown: Render committed assistant prose as rich Markdown.
        out: Output stream (defaults to ``sys.stdout``).
        palette: Semantic slot colors (R-THEME-1); ``None`` = default theme.
        animations: R-THEME-4 motion gate (False → static spinner).
        shell_marks: Emit OSC 133 zone marks around committed turns
            (``[tui] shell_integration``); off by default.
    """
    frontend = InlineFrontend(
        lane, out=out, band_height=band_height, markdown=markdown,
        palette=palette, animations=animations, shell_marks=shell_marks,
    )
    asyncio.run(frontend.run(initial_task=initial_task))

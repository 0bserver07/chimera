"""The live region under a lane's transcript: uncommitted tail + heartbeat.

Progressive block commitment (R-REN-6) writes *completed* markdown blocks to
the transcript log and keeps the still-changing remainder in
:attr:`~chimera.tui.render.LaneTranscript.live_tail`. This widget is the
pane-layer surface that makes that remainder — and the R-FOLD-1 reasoning
heartbeat — visible while it is still mutable:

- It sits **below** the transcript log, positionally fixed: when the user
  scrolls up to read (the log's anchor detaches, R-VIEW-1), the live region
  keeps updating in place instead of vanishing off-screen with the tail.
- The tail renders through the same renderable path as committed prose
  (:func:`~chimera.tui.render.assistant_renderable`), viewed through
  :func:`~chimera.tui.markdown_stream.live_tail_view` so a partial closing
  fence never makes the render shrink.
- Height is capped (:data:`LIVE_TAIL_MAX_LINES` visual lines) and the view is
  **bottom-anchored**: when the tail is taller, the oldest lines give way to a
  dim ``… +N lines`` marker at the top (:class:`TailCrop`), so the layout
  never jumps and the live edge — the newest text — is always the visible
  part.
- Everything here is display state only (R-VIEW-3): nothing this widget shows
  is ever written to a transcript sink or to ``Lane.record``. On commit /
  turn end / cancel / clear the region simply empties and hides.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.console import Group
from rich.measure import Measurement
from rich.segment import Segment
from rich.text import Text

try:
    from textual.widgets import Static
except ImportError as exc:  # pragma: no cover - mirrors chimera.tui.logview
    raise ImportError(
        "The Chimera TUI needs the 'tui' extra:\n"
        "  pip install 'chimera-run[tui]'   (or: pip install textual)"
    ) from exc

from chimera.tui.markdown_stream import live_tail_view
from chimera.tui.render import assistant_renderable

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult

__all__ = ["LIVE_TAIL_MAX_LINES", "LiveRegion", "TailCrop"]

#: Height cap for the live region, in visual (wrapped) lines. Bounded so a
#: long streaming block can never squeeze the transcript log away.
LIVE_TAIL_MAX_LINES = 6


class TailCrop:
    """Bottom-anchored view of a renderable, top-ellipsized past a line cap.

    Renders the wrapped *renderable* and keeps only its **last**
    ``max_lines - 1`` visual lines behind a dim ``… +N lines`` marker when it
    is taller than *max_lines* (shorter content passes through untouched).
    CSS ``max-height`` would crop the *bottom* — exactly the live edge a
    streaming tail must keep visible — so the crop happens here, at render
    time, where wrapped line counts are real.

    Args:
        renderable: Any rich renderable (markdown tail, heartbeat text, or a
            group of both).
        max_lines: Total visual lines to emit, marker line included.
    """

    def __init__(self, renderable: Any, max_lines: int = LIVE_TAIL_MAX_LINES) -> None:
        self.renderable = renderable
        self.max_lines = max(2, max_lines)  # marker + at least one live line

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement.get(console, options, self.renderable)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        opts = options.reset_height()
        lines = console.render_lines(self.renderable, opts, pad=False)
        if len(lines) > self.max_lines:
            keep = self.max_lines - 1
            marker = Text(f"… +{len(lines) - keep} lines", style="dim")
            lines = [*console.render_lines(marker, opts, pad=False), *lines[-keep:]]
        for line in lines:
            yield from line
            yield Segment.line()


class LiveRegion(Static):
    """Ephemeral display strip: live markdown tail and/or thinking heartbeat.

    Hidden whenever it has nothing to show, so idle panes spend no height on
    it. Content arrives via :meth:`show` (the pane calls it on every assistant
    event and on heartbeat pulses) and leaves via :meth:`clear_live`. The
    widget never touches a transcript sink — its content is unrecoverable by
    design once cleared (R-VIEW-3).
    """

    DEFAULT_CSS = """
    LiveRegion { height: auto; padding: 0 1; }
    """

    def __init__(self, *, max_lines: int = LIVE_TAIL_MAX_LINES, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self.max_lines = max_lines
        #: Last shown (tail view, heartbeat) — introspection for tests/status.
        self.tail_text = ""
        self.heartbeat_text = ""
        self.display = False

    def show(self, *, tail: str = "", heartbeat: str = "", markdown: bool = True) -> None:
        """Render the current live state; hide when both parts are empty.

        Args:
            tail: Uncommitted streaming source (``LaneTranscript.live_tail``).
                Displayed through :func:`live_tail_view` and the same
                renderable path as committed prose.
            heartbeat: The R-FOLD-1 heartbeat line, or ``""`` when reasoning
                is not accumulating. Rendered below the tail when both exist
                (interleaved thinking mid-prose keeps the pending tail
                visible).
            markdown: Render the tail as markdown (matches the transcript's
                setting).
        """
        view = live_tail_view(tail) if tail.strip() else ""
        if not view.strip():
            view = ""
        if not view and not heartbeat:
            self.clear_live()
            return
        if (view, heartbeat) == (self.tail_text, self.heartbeat_text) and self.display:
            return  # unchanged: skip the relayout entirely
        self.tail_text, self.heartbeat_text = view, heartbeat
        parts: list[Any] = []
        if view:
            parts.append(assistant_renderable(view, markdown=markdown))
        if heartbeat:
            parts.append(Text(heartbeat, style="dim italic"))
        inner = parts[0] if len(parts) == 1 else Group(*parts)
        self.update(TailCrop(inner, self.max_lines))
        self.display = True

    def clear_live(self) -> None:
        """Empty and hide the region (commit / turn end / cancel / clear)."""
        if not self.display and not self.tail_text and not self.heartbeat_text:
            return
        self.tail_text = ""
        self.heartbeat_text = ""
        self.update("")
        self.display = False

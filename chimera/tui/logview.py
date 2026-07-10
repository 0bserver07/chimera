"""Follow-mode transcript log shared by the Chimera TUIs.

Textual's stock ``RichLog`` force-scrolls to the bottom on every ``write()``
while ``auto_scroll`` is on — and its ``scroll_end`` re-attaches a released
anchor — so during a streaming turn the viewport yanks back down the moment
the user scrolls up to read, and every incoming event fights a text-selection
drag. This is the "wonky scroll" failure mode; the spec's contract is
sticky-bottom *with an escape hatch* (tui-ux-refinements §8, R-VIEW-1).

``TranscriptLog`` inverts the mechanism: ``auto_scroll`` stays off and
following is delegated to Textual's *anchor*, which the compositor honours on
every reflow. That yields the full contract natively:

- while anchored, new content keeps the view pinned to the tail;
- the user scrolling up (wheel, keys, scrollbar grab) releases the anchor, so
  the view freezes where they are reading;
- scrolling back to the bottom re-attaches it (``Widget._check_anchor``);
- a mouse press in the log releases it, so drag-selecting streamed text is
  never yanked; on release the log re-follows only when that press was a
  plain click by a user who was already following (or who is at the tail),
  never when a selection was made or they were parked mid-transcript.

Hidden panes (the multiplexer's tabbed mode) need no special casing: the
compositor re-pins an attached anchor on the reveal reflow, and a released
one keeps the user's reading position.
"""
from __future__ import annotations

from typing import Any

try:
    from textual.widgets import RichLog
except ImportError as exc:  # pragma: no cover - mirrors chimera.tui.app
    raise ImportError(
        "The Chimera TUI needs the 'tui' extra:\n"
        "  pip install 'chimera-run[tui]'   (or: pip install textual)"
    ) from exc

__all__ = ["TranscriptLog"]


class TranscriptLog(RichLog):
    """A ``RichLog`` that follows the tail only while the user is at it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Following is the anchor's job: RichLog's own auto_scroll would call
        # scroll_end on every write, which force-re-attaches a released anchor
        # and defeats the whole escape hatch.
        kwargs.setdefault("auto_scroll", False)
        super().__init__(*args, **kwargs)
        self._was_following = False

    def on_mount(self) -> None:
        self.anchor()

    def jump_to_tail(self) -> None:
        """Re-attach and pin to the bottom (for user-initiated content).

        Submitting a task or steer should always show the echo, even if the
        user had scrolled up — matching every terminal's own-input behavior.
        """
        self.anchor()  # anchor() also scrolls to the end immediately

    def clear(self) -> Any:
        """Clear and restart pinned — an emptied transcript follows again."""
        result = super().clear()
        self.anchor()
        return result

    def _following(self) -> bool:
        # ``_anchor_released`` is private Textual state with no public reader;
        # if a future version renames it, default to "released" so the failure
        # mode is "a click doesn't force re-follow", never a surprise yank.
        return self.is_anchored and not getattr(self, "_anchor_released", True)

    def on_mouse_down(self) -> None:
        # A press starts reading or a selection drag — stop following so
        # streaming writes can't move the view under the cursor. The event is
        # not stopped: the screen still runs its text-selection machinery.
        self._was_following = self._following()
        self.release_anchor()

    def on_mouse_up(self) -> None:
        # Deferred so the screen has settled selection state for this click.
        self.call_after_refresh(self._refollow_after_press)

    def _refollow_after_press(self) -> None:
        if getattr(self, "text_selection", None) is not None:
            return  # drag-selected: stay detached for reading/copying
        if self._was_following or self.is_vertical_scroll_end:
            self.anchor()

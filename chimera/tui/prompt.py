"""Shared prompt widget for the Chimera TUIs — multi-line input + slash autocomplete.

Implements spec §13.5/§13.6 for both the single-agent TUI and the multiplexer:

- **Multi-line entry** on a Textual ``TextArea``: ``Enter`` submits, ``Ctrl+J``
  (and ``Shift+Enter`` on terminals that report it) inserts a line break.
- **History recall**: ``Up`` on the first line / ``Down`` on the last line walk
  prior submissions; the in-progress draft is preserved.
- **Slash autocomplete**: while the input is a ``/`` prefix, the frontends show
  the filtered command catalog (via :func:`filter_commands`) and ``Tab``
  completes the longest common prefix (:func:`complete_command`).

The pure helpers are widget-free so the completion table is exhaustively
unit-testable; :class:`PromptArea` is the Textual widget both apps mount as
``#prompt``. It exposes a ``value`` property (mirroring ``Input``) so callers
and tests read/write it uniformly.
"""
from __future__ import annotations

import os
from typing import Any

try:
    from textual.message import Message
    from textual.widgets import TextArea
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The Chimera TUI needs the 'tui' extra:\n"
        "  pip install 'chimera-run[tui]'   (or: pip install textual)"
    ) from exc

__all__ = ["PromptArea", "filter_commands", "complete_command"]


def filter_commands(text: str, catalog: list[str]) -> list[str]:
    """Commands from *catalog* matching a ``/`` prefix being typed.

    Returns ``[]`` unless *text* is a single ``/``-word (no spaces yet) — once
    arguments start, completion is over.
    """
    stripped = text.strip()
    if not stripped.startswith("/") or " " in stripped or "\n" in text.strip("\n"):
        return []
    return sorted(c for c in catalog if c.startswith(stripped))


def complete_command(text: str, catalog: list[str]) -> str:
    """Tab-complete *text* against *catalog* (longest common prefix).

    A unique match completes fully (with a trailing space, ready for args).
    Multiple matches extend to their common prefix. No match returns *text*
    unchanged.
    """
    matches = filter_commands(text, catalog)
    if not matches:
        return text
    if len(matches) == 1:
        return matches[0] + " "
    prefix = os.path.commonprefix(matches)
    return prefix if len(prefix) > len(text.strip()) else text


class PromptArea(TextArea):
    """Multi-line prompt: Enter submits, Ctrl+J newline, Up/Down history, Tab completes."""

    # NOTE: no custom ``Changed`` — Textual's TextArea posts its own
    # ``TextArea.Changed`` on every edit (shadowing it breaks the widget's
    # internals, the same trap as shadowing App attrs). Frontends listen for
    # ``TextArea.Changed`` to drive the autocomplete hint line.

    class Submitted(Message):
        """Posted when the user submits (Enter)."""

        def __init__(self, prompt: PromptArea, value: str) -> None:
            super().__init__()
            self.prompt = prompt
            self.value = value

        @property
        def control(self) -> PromptArea:
            return self.prompt

    def __init__(self, *, commands: list[str] | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("tab_behavior", "focus")  # Tab is completion, not indent
        super().__init__(**kwargs)
        self.commands: list[str] = list(commands or [])
        self._history: list[str] = []
        self._hist_idx: int | None = None
        self._draft = ""

    # -- Input-compatible surface ----------------------------------------
    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, new: str) -> None:
        self.text = new

    # -- history ----------------------------------------------------------
    def remember(self, submission: str) -> None:
        """Record a submission for Up/Down recall."""
        if submission.strip() and (not self._history or self._history[-1] != submission):
            self._history.append(submission)
        self._hist_idx = None
        self._draft = ""

    def _recall(self, direction: int) -> bool:
        if not self._history:
            return False
        if self._hist_idx is None:
            if direction > 0:
                return False  # Down with no recall in progress
            self._draft = self.text
            self._hist_idx = len(self._history) - 1
        else:
            nxt = self._hist_idx + direction
            if nxt >= len(self._history):
                self._hist_idx = None
                self.text = self._draft
                self.move_cursor(self.document.end)
                return True
            if nxt < 0:
                return True  # already at the oldest
            self._hist_idx = nxt
        self.text = self._history[self._hist_idx]
        self.move_cursor(self.document.end)
        return True

    # -- key handling -------------------------------------------------------
    async def _on_key(self, event: Any) -> None:
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text
            self.post_message(self.Submitted(self, text))
            return
        if key in ("ctrl+j", "shift+enter"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if key == "tab" and self.text.lstrip().startswith("/"):
            event.stop()
            event.prevent_default()
            completed = complete_command(self.text, self.commands)
            if completed != self.text:
                self.text = completed
                self.move_cursor(self.document.end)
            return
        if key == "up" and self.cursor_location[0] == 0:
            if self._recall(-1):
                event.stop()
                event.prevent_default()
                return
        if key == "down" and self.cursor_location[0] == self.document.line_count - 1:
            if self._hist_idx is not None and self._recall(+1):
                event.stop()
                event.prevent_default()
                return
        await super()._on_key(event)

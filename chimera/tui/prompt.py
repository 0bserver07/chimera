"""Shared prompt widget for the Chimera TUIs — multi-line input + slash autocomplete.

Implements spec §13.5/§13.6 for both the single-agent TUI and the multiplexer:

- **Multi-line entry** on a Textual ``TextArea``: ``Enter`` submits, ``Ctrl+J``
  (and ``Shift+Enter`` on terminals that report it) inserts a line break.
- **History recall**: ``Up`` on the first line / ``Down`` on the last line walk
  prior submissions; the in-progress draft is preserved.
- **Slash autocomplete**: while the input is a ``/`` prefix, the frontends show
  the filtered command catalog (via :func:`filter_commands`) and ``Tab``
  completes the longest common prefix (:func:`complete_command`).
- **Paste chips** (R-FOLD-6): a paste over the configured size collapses to a
  ``[Pasted #1 ~420 lines]`` chip instead of burying the composer. The chip is
  an **atomic edit unit** — cursor movement, word-nav and delete treat it as
  one token and never expose its interior — and the full text rides with the
  message on submit (:attr:`PromptArea.Submitted.value`), while the chip form
  is what history recall stores (:attr:`PromptArea.Submitted.raw`).

The pure helpers are widget-free so the completion table and the chip grammar
are exhaustively unit-testable; :class:`PromptArea` is the Textual widget both
apps mount as ``#prompt``. It exposes a ``value`` property (mirroring
``Input``) so callers and tests read/write it uniformly.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

try:
    from textual.message import Message
    from textual.widgets import TextArea
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The Chimera TUI needs the 'tui' extra:\n"
        "  pip install 'chimera-run[tui]'   (or: pip install textual)"
    ) from exc

__all__ = [
    "CHIP_RE",
    "PasteSettings",
    "PromptArea",
    "atomic_delete",
    "atomic_move",
    "chip_label",
    "chip_spans",
    "complete_command",
    "expand_chips",
    "filter_commands",
    "load_paste_settings",
    "should_collapse",
]


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


# --------------------------------------------------------------------------
# Paste chips (R-FOLD-6) — pure grammar
# --------------------------------------------------------------------------

#: A chip in the composer text. The ``#N`` is what makes a chip *identifiable*:
#: two pastes of the same shape must not expand to each other's text.
CHIP_RE = re.compile(r"\[Pasted #(\d+) ~[^\]\n]*\]")


@dataclass(frozen=True)
class PasteSettings:
    """When a paste collapses to a chip (R-FOLD-6).

    Both caps bind, whichever hits first — a 400-line paste and a single
    12 kB line are equally unreadable in a composer. ``0`` on a cap disables
    that cap; ``0`` on both means pastes always insert verbatim (the
    pre-chip behavior).

    Attributes:
        lines: Collapse above this many lines (``[tui] paste_chip_lines``).
        chars: Collapse above this many characters
            (``[tui] paste_chip_chars``).
    """

    lines: int = 8
    chars: int = 1000


def _positive_int(value: Any, fallback: int) -> int:
    """A non-negative int from config, or *fallback* for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(0, parsed)


def paste_settings_from_config(tui: Mapping[str, Any]) -> PasteSettings:
    """Resolve :class:`PasteSettings` from a ``[tui]`` config table.

    Args:
        tui: The merged ``tui`` section (may be empty or hold junk values —
            a malformed knob falls back to its default rather than raising).

    Returns:
        The resolved settings.
    """
    default = PasteSettings()
    return PasteSettings(
        lines=_positive_int(tui.get("paste_chip_lines"), default.lines),
        chars=_positive_int(tui.get("paste_chip_chars"), default.chars),
    )


def load_paste_settings(
    project_dir: str | os.PathLike[str] | None = None,
    *,
    home: str | os.PathLike[str] | None = None,
) -> PasteSettings:
    """Read the paste thresholds from the unified config chain.

    The same XDG < user < project chain as themes, keybinds and the status
    line (:mod:`chimera.config.user_config`). Discovery is best-effort: a
    broken config file degrades to the defaults rather than blocking a launch.

    Args:
        project_dir: Project root (default: cwd).
        home: Home-directory override (tests).

    Returns:
        The resolved settings.
    """
    try:
        from chimera.config.user_config import load_tui_config

        tui = load_tui_config(project_dir, home=home)
    except Exception:  # noqa: BLE001 — config discovery must not block a launch
        return PasteSettings()
    return paste_settings_from_config(tui)


def should_collapse(text: str, settings: PasteSettings) -> bool:
    """Whether a pasted *text* is big enough to collapse into a chip.

    Args:
        text: The pasted text.
        settings: The resolved thresholds.

    Returns:
        True when either enabled cap is exceeded.
    """
    if settings.lines and text.count("\n") + 1 > settings.lines:
        return True
    return bool(settings.chars) and len(text) > settings.chars


def chip_label(index: int, text: str) -> str:
    """The chip standing in for a pasted *text*.

    Multi-line pastes are measured in lines (what the user sees); a single
    huge line is measured in characters, because ``~1 line`` would say
    nothing about it.

    Args:
        index: 1-based paste number within the session — the chip's identity.
        text: The full pasted text.

    Returns:
        E.g. ``"[Pasted #1 ~420 lines]"`` or ``"[Pasted #2 ~12000 chars]"``.
    """
    lines = text.count("\n") + 1
    size = f"{lines} lines" if lines > 1 else f"{len(text)} chars"
    return f"[Pasted #{index} ~{size}]"


def chip_spans(line: str) -> list[tuple[int, int]]:
    """The ``(start, end)`` column spans of every chip in one line.

    Args:
        line: A single line of composer text (chips never span lines).

    Returns:
        Half-open spans, ascending.
    """
    return [(m.start(), m.end()) for m in CHIP_RE.finditer(line)]


def _crossed_chip(
    line: str, col: int, direction: int,
) -> tuple[int, int] | None:
    """The chip a move/delete at *col* would enter, if any.

    A cursor *inside* a chip (a click, a recalled draft) counts in either
    direction — the interior is never a legal resting place.
    """
    for start, end in chip_spans(line):
        if start < col < end:
            return (start, end)
        if direction < 0 and col == end:
            return (start, end)
        if direction > 0 and col == start:
            return (start, end)
    return None


def atomic_move(line: str, col: int, direction: int) -> int | None:
    """The column a cursor move must land on to hop a chip whole.

    Args:
        line: The line the cursor is on.
        col: Current column.
        direction: ``-1`` for left/word-left, ``+1`` for right/word-right.

    Returns:
        The new column when a chip is in the way, else ``None`` (the caller
        then lets the editor's own movement run).
    """
    span = _crossed_chip(line, col, direction)
    if span is None:
        return None
    return span[0] if direction < 0 else span[1]


def atomic_delete(
    line: str, col: int, direction: int,
) -> tuple[int, int] | None:
    """The span a delete must remove so a chip goes whole or not at all.

    Args:
        line: The line the cursor is on.
        col: Current column.
        direction: ``-1`` for backspace, ``+1`` for delete-forward.

    Returns:
        The ``(start, end)`` columns to delete when a chip is in the way,
        else ``None`` (the caller lets the editor delete one character).
    """
    return _crossed_chip(line, col, direction)


def expand_chips(text: str, store: Mapping[str, str]) -> str:
    """Replace every known chip in *text* with the paste it stands for.

    Only *intact* chips expand: text that merely looks chip-ish, or a chip
    the store never saw (a recalled draft from another session), is left
    alone — a half-expanded paste would be worse than none.

    Args:
        text: The composer text.
        store: chip token → the full pasted text.

    Returns:
        The text with known chips expanded.
    """
    if not store:
        return text
    return CHIP_RE.sub(lambda m: store.get(m.group(0), m.group(0)), text)


class PromptArea(TextArea):
    """Multi-line prompt: Enter submits, Ctrl+J newline, Up/Down history, Tab completes."""

    # NOTE: no custom ``Changed`` — Textual's TextArea posts its own
    # ``TextArea.Changed`` on every edit (shadowing it breaks the widget's
    # internals, the same trap as shadowing App attrs). Frontends listen for
    # ``TextArea.Changed`` to drive the autocomplete hint line.

    class Submitted(Message):
        """Posted when the user submits (Enter).

        Args:
            prompt: The widget that was submitted.
            value: What to send — paste chips **expanded** to their full text
                (R-FOLD-6: the full text rides with the message).
            raw: What was on screen, chips intact. Defaults to *value* when
                nothing was collapsed, so a chip-free submission is
                indistinguishable from before. Frontends store this in
                history so recall shows the chip, not a wall of text.
        """

        def __init__(
            self, prompt: PromptArea, value: str, raw: str | None = None,
        ) -> None:
            super().__init__()
            self.prompt = prompt
            self.value = value
            self.raw = value if raw is None else raw

        @property
        def control(self) -> PromptArea:
            return self.prompt

    def __init__(
        self,
        *,
        commands: list[str] | None = None,
        paste: PasteSettings | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("tab_behavior", "focus")  # Tab is completion, not indent
        super().__init__(**kwargs)
        self.commands: list[str] = list(commands or [])
        self._history: list[str] = []
        self._hist_idx: int | None = None
        self._draft = ""
        #: R-FOLD-6 thresholds; read from the config chain unless injected.
        self.paste_settings = paste if paste is not None else load_paste_settings()
        #: chip token → the full pasted text it stands for. Kept for the whole
        #: session so a recalled draft's chip still expands.
        self._pastes: dict[str, str] = {}

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

    # -- paste chips (R-FOLD-6) ---------------------------------------------
    @property
    def pastes(self) -> dict[str, str]:
        """The session's chip token → full-text map (read-only view)."""
        return dict(self._pastes)

    def submitted_text(self) -> str:
        """The composer's text with every known paste chip expanded."""
        return expand_chips(self.text, self._pastes)

    async def _on_paste(self, event: Any) -> None:
        """Collapse an oversized paste to an atomic chip (R-FOLD-6).

        Small pastes are simply *not handled here*: the framework dispatches
        every ``_on_paste`` in the MRO, so returning without preventing the
        default lets the editor's own handler insert the text exactly as
        before chips existed. Collapsing calls ``prevent_default()``, which
        stops that walk, so the raw text never reaches the document.
        """
        text = getattr(event, "text", "")
        if not text or not should_collapse(text, self.paste_settings):
            return
        event.stop()
        event.prevent_default()
        chip = chip_label(len(self._pastes) + 1, text)
        self._pastes[chip] = text
        self.insert(chip)

    def _atomic_key(self, key: str) -> bool:
        """Apply chip-atomic movement/deletion for *key*; True when handled.

        Chips are single-line by construction, so all of this is columns on
        the cursor's own line.
        """
        if not self._pastes or self.selection.start != self.selection.end:
            return False  # a live selection is the editor's business
        row, col = self.cursor_location
        line = self.document.get_line(row)
        direction = -1 if key in ("left", "ctrl+left", "backspace") else 1
        if key in ("left", "right", "ctrl+left", "ctrl+right"):
            target = atomic_move(line, col, direction)
            if target is None:
                return False
            self.move_cursor((row, target))
            return True
        span = atomic_delete(line, col, direction)
        if span is None:
            return False
        self.delete((row, span[0]), (row, span[1]))
        return True

    # -- key handling -------------------------------------------------------
    async def _on_key(self, event: Any) -> None:
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            raw = self.text
            self.post_message(self.Submitted(self, expand_chips(raw, self._pastes), raw))
            return
        if key in ("left", "right", "ctrl+left", "ctrl+right", "backspace", "delete"):
            # A chip moves/deletes whole — its interior is never entered.
            if self._atomic_key(key):
                event.stop()
                event.prevent_default()
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

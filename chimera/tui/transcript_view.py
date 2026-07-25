"""The full-screen transcript overlay: the universal fold target (R-FOLD-7).

Every elision in the live panes is display-only — the lane's session record
(:attr:`chimera.tui.lane.Lane.transcript_lines`) always kept the whole thing
(R-FOLD-3). This module is where a user reads *that*: a pager over the
complete, untruncated transcript, opened from the registry-owned
``show_transcript`` action whose currently-bound key every ``… +N lines …``
marker already advertises (:func:`chimera.tui.render._elision_marker`'s
``full_hint``).

Two rendering modes (R-VIEW-4):

- **rich** (default) — a dim line-number gutter, so a match found by the
  search filter can be located in the record;
- **plain** — no gutter, no color, no padding: a select-and-paste (or
  screen-scrape) surface, one transcript line per row.

Navigation is the framework's own scroll bindings (the log is focusable:
↑/↓/PgUp/PgDn/Home/End), plus a live search *filter* — typing narrows the
pager to matching lines with their original line numbers, so "where did that
tool call go" is one query rather than a scroll hunt. The filter is a plain
case-insensitive substring match; :func:`match_lines` is pure, so the
behavior is pinned without a terminal.

The pure helpers (:func:`transcript_lines`, :func:`match_lines`,
:func:`view_rows`) hold every decision worth testing; :class:`TranscriptScreen`
is the thin framework shell around them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, RichLog, Static
from rich.text import Text

from chimera.tui.keys import ResolvedBinding, apply_keymap, build_bindings
from chimera.tui.theme import Palette

if TYPE_CHECKING:
    from chimera.tui.lane import Lane

__all__ = [
    "TranscriptScreen",
    "match_lines",
    "transcript_lines",
    "view_rows",
]

#: The unconfigured palette (same posture as :mod:`chimera.tui.render`): its
#: slot values are the styles this overlay would otherwise hardcode.
_DEFAULT_PALETTE = Palette()


def transcript_lines(lane: Lane) -> list[str]:
    """The lane's complete, untruncated transcript, one entry per line.

    Reads the session record — which is written with elision **off**
    (R-FOLD-3), so nothing here was ever truncated — and splits the embedded
    newlines a single record entry may carry (a multi-line tool result is one
    entry).

    Args:
        lane: The lane whose record to read. Not mutated: streaming text that
            has not committed yet is deliberately left out, so the overlay
            shows committed history only.

    Returns:
        The transcript's lines, in order (empty when nothing has been
        recorded).
    """
    out: list[str] = []
    for entry in lane.transcript_lines:
        out.extend(str(entry).split("\n"))
    return out


def match_lines(lines: Sequence[str], query: str) -> list[int]:
    """Indices of the lines matching *query* (case-insensitive substring).

    Args:
        lines: The transcript lines.
        query: The search text; blank matches everything.

    Returns:
        The matching indices, ascending. A blank query returns every index,
        so callers can render filtered and unfiltered views through one path.
    """
    needle = query.strip().lower()
    if not needle:
        return list(range(len(lines)))
    return [i for i, line in enumerate(lines) if needle in line.lower()]


def view_rows(
    lines: Sequence[str],
    indices: Sequence[int],
    *,
    plain: bool = False,
    palette: Palette | None = None,
) -> list[Text]:
    """Build the pager's rows: gutter + text, or bare text in plain mode.

    Args:
        lines: The full transcript lines.
        indices: Which of them to show, in order (from :func:`match_lines`).
        plain: Copy-friendly mode (R-VIEW-4) — no gutter, no color, so a
            terminal selection over the pager yields exactly the transcript
            text.
        palette: Semantic slot colors; ``None`` uses the built-in theme.

    Returns:
        One renderable per requested line.
    """
    if plain:
        return [Text(lines[i]) for i in indices]
    pal = palette if palette is not None else _DEFAULT_PALETTE
    gutter_style = pal.style("chrome.elision")
    width = len(str(len(lines))) if lines else 1
    return [
        Text.assemble((f"{i + 1:>{width}} │ ", gutter_style), (lines[i], ""))
        for i in indices
    ]


class TranscriptScreen(Screen):
    """Pager over one lane's complete transcript (R-FOLD-7 / R-VIEW-4).

    Args:
        lane: The lane whose record to page through.
        palette: Semantic slot colors (R-THEME-1); ``None`` = the default
            theme, whose slots reproduce the pre-theme styles.
        keymap: The app's resolved keymap, so the overlay's own keys honor
            ``tui.keybinds`` overrides (R-KEY-2) like every other surface.
        plain: Start in copy-friendly plain mode.
    """

    #: Pager-context bindings from the one registry (R-KEY-1): close, the
    #: plain-mode toggle, and the search filter. Instance-level overrides are
    #: applied in ``__init__`` from the app's resolved keymap.
    BINDINGS = build_bindings(context="pager")

    CSS = """
    #transcript-title { height: 1; background: $primary; color: $text; padding: 0 1; }
    #transcript-body { height: 1fr; padding: 0 1; }
    /* Plain mode drops the padding too: a selection must not pick up
       leading spaces the transcript never had (R-VIEW-4). */
    #transcript-body.plain { padding: 0; }
    #transcript-search { height: 3; }
    """

    def __init__(
        self,
        lane: Lane,
        *,
        palette: Palette | None = None,
        keymap: Mapping[str, ResolvedBinding] | None = None,
        plain: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._lane = lane
        self._palette = palette if palette is not None else _DEFAULT_PALETTE
        self._plain = plain
        self._query = ""
        #: Snapshot of the record taken at open time: a turn streaming in the
        #: background must not shift rows out from under the reader.
        self._lines: list[str] = transcript_lines(lane)
        if keymap is not None:
            apply_keymap(self._bindings, keymap, context="pager")

    # -- layout -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title_text(), id="transcript-title")
            # auto_scroll off: a transcript is read from wherever you land,
            # and a filter rebuild must not yank the view to the bottom.
            yield RichLog(
                id="transcript-body", wrap=True, markup=False, highlight=False,
                auto_scroll=False,
            )
            yield Input(placeholder="search…", id="transcript-search")
            yield Footer()

    def on_mount(self) -> None:
        self._rebuild()
        # Focus the log, not the filter: the pager keys (↑/↓/PgUp/PgDn/
        # Home/End) are the framework's own scroll bindings on the focused
        # scrollable, and they must work the instant the overlay opens.
        self.query_one("#transcript-body", RichLog).focus()

    # -- rendering ---------------------------------------------------------
    def _title_text(self) -> Text:
        total = len(self._lines)
        mode = "plain" if self._plain else "rich"
        shown = len(match_lines(self._lines, self._query))
        found = "" if not self._query.strip() else f" · {shown} matching"
        return Text(
            f" transcript · {self._lane.label} ({self._lane.config.model}) · "
            f"{total} lines{found} · [{mode}]",
            style="bold",
        )

    # NOTE: ``_rebuild``, NOT ``_render`` — the framework's ``Widget._render``
    # is what produces a widget's own visual, and shadowing it makes the
    # screen render itself by querying its children (which raises during the
    # first paint). Same trap as shadowing ``COMMANDS`` on the app or
    # ``Changed`` on the composer; the picker screens use this name too.
    def _rebuild(self) -> None:
        """Rebuild the pager body for the current filter and rendering mode."""
        log = self.query_one("#transcript-body", RichLog)
        log.set_class(self._plain, "plain")
        log.clear()
        log.scroll_home(animate=False)
        self.query_one("#transcript-title", Static).update(self._title_text())
        if not self._lines:
            log.write(Text("(nothing recorded yet)", style="dim italic"))
            return
        indices = match_lines(self._lines, self._query)
        if not indices:
            log.write(Text(f"(no line matches {self._query!r})", style="dim italic"))
            return
        for row in view_rows(
            self._lines, indices, plain=self._plain, palette=self._palette,
        ):
            log.write(row)

    # -- events ------------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-filter the pager as the search text is typed."""
        if event.input.id != "transcript-search":
            return
        self._query = event.value
        self._rebuild()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the filter returns to the pager, keeping the filter."""
        if event.input.id != "transcript-search":
            return
        self.query_one("#transcript-body", RichLog).focus()

    # -- actions -----------------------------------------------------------
    def action_plain_mode(self) -> None:
        """Toggle copy-friendly plain rendering (R-VIEW-4)."""
        self._plain = not self._plain
        self._rebuild()

    def action_search(self) -> None:
        """Focus the search filter."""
        self.query_one("#transcript-search", Input).focus()

    def action_close(self) -> None:
        """Leave the filter first, then the overlay.

        Esc inside the search box returns to the pager (the filter survives,
        so a narrowed view is not lost by a stray key); Esc in the pager pops
        the overlay.
        """
        search = self.query_one("#transcript-search", Input)
        if search.has_focus:
            self.query_one("#transcript-body", RichLog).focus()
            return
        self.app.pop_screen()

"""One universal fuzzy-select modal for the Chimera TUIs (spec §9, R-OVER-2).

Every list-pick surface (the cohort picker, future model/theme pickers, a
command palette) is the same interaction: a modal list, type-to-filter,
Enter confirms, Esc cancels. This module implements it once:

- :func:`score_text` / :func:`score_item` / :func:`rank_items` — the pure,
  widget-free weighted scorer: exact match > exact prefix > word-boundary >
  substring, label matches outrank description/category matches, and ties
  keep the caller's insertion order (stable ranking).
- :class:`SelectItem` — one row: value, label, dim description line,
  right-aligned hint, optional category group, optional stable option id.
- :class:`FuzzySelectScreen` — the Textual ``ModalScreen``. It returns the
  chosen item's ``value`` through screen dismissal (``None`` on cancel), so
  callers pass a ``push_screen`` callback (or ``push_screen_wait``) and never
  touch widget internals. The prior screen's focus is restored by the screen
  stack when the modal pops (R-OVER-1 basics). Items with categories render
  under bold group headers while the filter is empty and flatten into one
  ranked list while filtering. The footer hint line is derived from the
  screen's live key bindings so it cannot drift from a rebind.

Reusable by design: no imports from the multiplexer or any specific app.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

try:
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text
    from textual import on
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Input, OptionList, Static
    from textual.widgets.option_list import Option
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The Chimera fuzzy-select dialog needs the 'tui' extra:\n"
        "  pip install 'chimera-run[tui]'   (or: pip install textual)"
    ) from exc

__all__ = [
    "SelectItem",
    "FuzzySelectScreen",
    "score_text",
    "score_item",
    "rank_items",
    "hints_from_bindings",
]


# Score tiers, strongest first. The gaps leave room for future refinements
# (e.g. a short-text bonus) without reshuffling the tier order.
_SCORE_EXACT = 1000
_SCORE_PREFIX = 800
_SCORE_WORD = 600
_SCORE_SUBSTRING = 400


def score_text(query: str, text: str) -> int | None:
    """Score how well *query* matches *text* (higher is better).

    Tiers: exact match > exact prefix > word-boundary prefix (the query
    starts right after a non-alphanumeric character, e.g. ``-``, ``_``,
    space, ``/``) > substring. Case-insensitive.

    Args:
        query: The needle, already stripped of surrounding whitespace.
        text: The haystack.

    Returns:
        A tier score, ``0`` for an empty query (matches everything equally),
        or ``None`` when *query* does not occur in *text* at all.
    """
    q = query.casefold()
    if not q:
        return 0
    t = text.casefold()
    if not t:
        return None
    if t == q:
        return _SCORE_EXACT
    if t.startswith(q):
        return _SCORE_PREFIX
    idx = t.find(q)
    if idx == -1:
        return None
    if not t[idx - 1].isalnum():
        return _SCORE_WORD
    return _SCORE_SUBSTRING


def score_item(query: str, item: SelectItem) -> int | None:
    """Score *item* against a whitespace-tokenized *query*.

    Every token must match somewhere in the item (label, description,
    category, or ``search_text``) or the item is excluded. A label match is
    weighted double so it outranks auxiliary-field matches; per-token scores
    sum, so multi-token queries reward items matching all tokens strongly.

    Args:
        query: Raw filter text; split on whitespace into AND-ed tokens.
        item: The candidate row.

    Returns:
        The summed score, ``0`` for an empty query, or ``None`` when any
        token fails to match the item.
    """
    tokens = query.split()
    if not tokens:
        return 0
    total = 0
    for token in tokens:
        label_score = score_text(token, item.label)
        if label_score is not None:
            total += label_score * 2
            continue
        aux_scores = [
            s
            for s in (
                score_text(token, item.description),
                score_text(token, item.category),
                score_text(token, item.search_text),
            )
            if s is not None
        ]
        if not aux_scores:
            return None
        total += max(aux_scores)
    return total


def rank_items(items: Sequence[SelectItem], query: str) -> list[SelectItem]:
    """Filter and rank *items* for *query*, preserving order among ties.

    Args:
        items: Candidate rows in the caller's preferred (tie-break) order.
        query: Raw filter text (empty keeps every item in original order).

    Returns:
        Matching items, best score first; equal scores keep insertion order.
    """
    scored: list[tuple[int, int, SelectItem]] = []
    for index, item in enumerate(items):
        score = score_item(query, item)
        if score is not None:
            scored.append((-score, index, item))
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _, _, item in scored]


@dataclass(frozen=True)
class SelectItem:
    """One selectable row in a :class:`FuzzySelectScreen`.

    Attributes:
        value: Opaque payload returned via screen dismissal when chosen.
        label: Primary display text; weighted double by the scorer.
        description: Dim second line under the label.
        hint: Right-aligned dim text on the label line (a key, a timestamp).
        category: Optional group; grouped headers show while the filter is
            empty and collapse into a flat ranked list while filtering.
        search_text: Extra invisible haystack for the scorer (aliases, tags).
        id: Optional stable Textual option id (must be unique when given).
    """

    value: Any
    label: str
    description: str = ""
    hint: str = ""
    category: str = ""
    search_text: str = ""
    id: str | None = None


#: Pretty glyphs for key names in the derived hint line.
_KEY_GLYPHS = {
    "escape": "Esc",
    "enter": "Enter",
    "up": "↑",
    "down": "↓",
    "pageup": "PgUp",
    "pagedown": "PgDn",
    "tab": "Tab",
}


def hints_from_bindings(
    bindings: Iterable[Any],
    extra: Sequence[tuple[str, str]] = (("Enter", "select"),),
) -> str:
    """Derive a footer hint line from live key bindings (cannot drift).

    Bindings sharing a description are merged (``↑/↓ move``); bindings
    without a description are skipped. *extra* appends structural keys that
    are not Bindings (Enter is the filter input's submit key).

    Args:
        bindings: ``Binding``-like objects with ``key`` and ``description``.
        extra: ``(key_label, description)`` pairs appended at the end.

    Returns:
        A ``·``-joined hint string, e.g. ``"↑/↓ move · Esc cancel · Enter select"``.
    """
    keys_by_description: dict[str, list[str]] = {}
    order: list[str] = []
    for binding in bindings:
        description = str(getattr(binding, "description", "") or "").strip()
        key = str(getattr(binding, "key", "") or "")
        if not description or not key:
            continue
        if description not in keys_by_description:
            keys_by_description[description] = []
            order.append(description)
        keys_by_description[description].append(_KEY_GLYPHS.get(key, key))
    parts = [f"{'/'.join(keys_by_description[d])} {d}" for d in order]
    parts.extend(f"{key_label} {description}" for key_label, description in extra)
    return " · ".join(parts)


class _Unset:
    """Sentinel distinguishing "no value" from a legitimate ``None`` value."""


_UNSET = _Unset()


class FuzzySelectScreen(ModalScreen[Any]):
    """Generic modal fuzzy-select: filter input over a ranked option list.

    Dismisses with the chosen item's ``value`` on Enter / option selection,
    or ``None`` on Esc. Focus stays on the filter input; ↑/↓/PgUp/PgDn move
    the list highlight (disabled group headers are skipped).

    Args:
        items: Rows to offer, in preferred tie-break order.
        title: Modal title line.
        placeholder: Filter input placeholder.
        initial: Pre-select the item whose ``value`` equals this.
    """

    CSS = """
    FuzzySelectScreen { align: center middle; }
    #select-dialog {
        width: 72%; min-width: 44; max-width: 110;
        height: auto; max-height: 80%;
        background: $surface; border: round $secondary;
    }
    #select-title { height: 1; background: $primary; color: $text; padding: 0 1; }
    #select-list { height: auto; max-height: 20; }
    #select-hints { height: 1; color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("up", "cursor_move(-1)", "move", show=False),
        Binding("down", "cursor_move(1)", "move", show=False),
        Binding("pageup", "cursor_move(-8)", "", show=False),
        Binding("pagedown", "cursor_move(8)", "", show=False),
        Binding("escape", "cancel", "cancel", show=False),
    ]

    def __init__(
        self,
        items: Sequence[SelectItem],
        *,
        title: str = "Select",
        placeholder: str = "Type to filter…",
        initial: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._items = list(items)
        self._title = title
        self._placeholder = placeholder
        self._initial = initial
        #: Row-index → item; ``None`` marks a (disabled) category header row.
        self._visible: list[SelectItem | None] = []

    # -- layout -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Vertical(id="select-dialog"):
            yield Static(Text(f" {self._title}", style="bold"), id="select-title")
            yield Input(placeholder=self._placeholder, id="select-filter")
            yield OptionList(id="select-list")
            yield Static(hints_from_bindings(self.BINDINGS), id="select-hints")

    def on_mount(self) -> None:
        self._rebuild("", keep_value=self._initial if self._initial is not None else _UNSET)
        self.query_one("#select-filter", Input).focus()

    # -- option building --------------------------------------------------
    def _rebuild(self, query: str, *, keep_value: Any = _UNSET) -> None:
        """Re-rank and re-render the option list for *query*.

        Args:
            query: Current filter text.
            keep_value: Prefer re-highlighting the row with this value;
                defaults to the currently highlighted value.
        """
        option_list = self.query_one("#select-list", OptionList)
        preferred = (
            keep_value if not isinstance(keep_value, _Unset) else self._highlighted_value()
        )
        ranked = rank_items(self._items, query)
        option_list.clear_options()
        self._visible = []
        grouped = not query.strip() and any(item.category for item in ranked)
        if grouped:
            by_category: dict[str, list[SelectItem]] = {}
            order: list[str] = []
            for item in ranked:
                if item.category not in by_category:
                    by_category[item.category] = []
                    order.append(item.category)
                by_category[item.category].append(item)
            for category in order:
                if category:
                    option_list.add_option(Option(Text(category, style="bold"), disabled=True))
                    self._visible.append(None)
                for item in by_category[category]:
                    option_list.add_option(Option(self._row(item), id=item.id))
                    self._visible.append(item)
        else:
            for item in ranked:
                option_list.add_option(Option(self._row(item), id=item.id))
                self._visible.append(item)
        self._highlight_value(preferred)

    @staticmethod
    def _row(item: SelectItem) -> Any:
        """Render one row: label + right-aligned hint, dim description below."""
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        cells: list[Any] = [Text(item.label, no_wrap=True, overflow="ellipsis")]
        if item.hint:
            grid.add_column(justify="right", no_wrap=True, overflow="ellipsis")
            cells.append(Text(item.hint, style="dim", no_wrap=True, overflow="ellipsis"))
        grid.add_row(*cells)
        if item.description:
            description = Text(
                item.description, style="dim", no_wrap=True, overflow="ellipsis"
            )
            return Group(grid, description)
        return grid

    # -- highlight bookkeeping ---------------------------------------------
    def _selectable_rows(self) -> list[int]:
        return [i for i, item in enumerate(self._visible) if item is not None]

    def _highlighted_value(self) -> Any:
        option_list = self.query_one("#select-list", OptionList)
        index = option_list.highlighted
        if index is not None and 0 <= index < len(self._visible):
            item = self._visible[index]
            if item is not None:
                return item.value
        return _UNSET

    def _highlight_value(self, value: Any) -> None:
        option_list = self.query_one("#select-list", OptionList)
        rows = self._selectable_rows()
        if not rows:
            return
        target = rows[0]
        if not isinstance(value, _Unset):
            for row in rows:
                item = self._visible[row]
                if item is not None and item.value == value:
                    target = row
                    break
        option_list.highlighted = target

    # -- events -------------------------------------------------------------
    @on(Input.Changed, "#select-filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._rebuild(event.value)

    @on(Input.Submitted, "#select-filter")
    def _filter_submitted(self, event: Input.Submitted) -> None:
        self._confirm()

    @on(OptionList.OptionSelected, "#select-list")
    def _option_selected(self, event: OptionList.OptionSelected) -> None:
        index = event.option_index
        if 0 <= index < len(self._visible):
            item = self._visible[index]
            if item is not None:
                self.dismiss(item.value)

    # -- actions -------------------------------------------------------------
    def action_cursor_move(self, delta: int) -> None:
        """Move the list highlight by *delta* selectable rows (skips headers)."""
        option_list = self.query_one("#select-list", OptionList)
        rows = self._selectable_rows()
        if not rows:
            return
        current = option_list.highlighted
        position = rows.index(current) if current in rows else 0
        position = max(0, min(len(rows) - 1, position + delta))
        option_list.highlighted = rows[position]

    def action_cancel(self) -> None:
        """Esc: dismiss without a choice."""
        self.dismiss(None)

    def _confirm(self) -> None:
        """Enter: dismiss with the highlighted item's value (no-op when empty)."""
        option_list = self.query_one("#select-list", OptionList)
        index = option_list.highlighted
        if index is None or not (0 <= index < len(self._visible)):
            return
        item = self._visible[index]
        if item is not None:
            self.dismiss(item.value)

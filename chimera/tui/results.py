"""In-UI cohort comparison view (spec §13.1) — the multiplexer's payoff on screen.

When lanes finish racing one task, the interesting artifact is *what each model
produced*. This screen surfaces it inside the TUI: a ranked scoreboard over a
per-lane diff viewer, so you compare outputs without digging into
``~/.chimera/cohorts/``. Reached via ``Ctrl+R`` / ``/results`` in the multiplexer;
``Tab`` / ``←→`` cycle lanes, ``Esc`` returns to the live panes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from rich.table import Table
    from rich.text import Text
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.screen import Screen
    from textual.widgets import Footer, RichLog, Static
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The Chimera comparison view needs the 'tui' extra:\n"
        "  pip install 'chimera-run[tui]'   (or: pip install textual)"
    ) from exc

from chimera.tui.theme import Palette
from chimera.tui.worddiff import Span, pair_runs, word_spans

if TYPE_CHECKING:
    from chimera.tui.cohort import Cohort

__all__ = [
    "ResultsScreen",
    "scoreboard_table",
    "render_diff",
    "split_diff_files",
    "split_rows",
    "word_diff_lines",
]

#: The unconfigured palette — its diff slots are the styles this module
#: hardcoded before themes existed, so ``palette=None`` renders identically.
_DEFAULT_PALETTE = Palette()


def split_diff_files(diff: str) -> list[tuple[str, str]]:
    """Split a multi-file unified diff into ``(filename, file_diff)`` chunks.

    Filenames come from the ``diff --git a/... b/...`` headers; text before the
    first header (or a non-git diff) is one chunk labelled ``(changes)``.
    """
    lines = diff.splitlines()
    files: list[tuple[str, list[str]]] = []
    current: list[str] = []
    name = "(changes)"
    for line in lines:
        if line.startswith("diff --git "):
            if current and any(part.strip() for part in current):
                files.append((name, current))
            current = [line]
            parts = line.split()
            name = parts[3][2:] if len(parts) >= 4 else "(file)"
        else:
            current.append(line)
    if current and any(part.strip() for part in current):
        files.append((name, current))
    return [(fname, "\n".join(body)) for fname, body in files]


def split_rows(file_diff: str) -> list[tuple[str, str, str]]:
    """Pair a unified file-diff's hunk lines into split-view rows.

    Returns ``(kind, left, right)`` rows: context lines mirror both sides;
    consecutive removal/addition runs are paired index-wise (the classic
    side-by-side alignment). ``kind`` is ``"ctx"``, ``"change"``, or ``"meta"``
    (headers/hunk markers, left column only).
    """
    rows: list[tuple[str, str, str]] = []
    removes: list[str] = []
    adds: list[str] = []

    def flush() -> None:
        for i in range(max(len(removes), len(adds))):
            rows.append((
                "change",
                removes[i] if i < len(removes) else "",
                adds[i] if i < len(adds) else "",
            ))
        removes.clear()
        adds.clear()

    for line in file_diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            adds.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removes.append(line[1:])
        else:
            flush()
            if line.startswith(("@@", "diff ", "index ", "+++", "---")):
                rows.append(("meta", line, ""))
            else:
                text = line[1:] if line.startswith(" ") else line
                rows.append(("ctx", text, text))
    flush()
    return rows


def scoreboard_table(cohort: Cohort) -> Table:
    """A ranked comparison table of the cohort's lanes (winner highlighted)."""
    table = Table(expand=True, show_edge=False, pad_edge=False)
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("lane", no_wrap=True)
    table.add_column("model", no_wrap=True)
    table.add_column("outcome", no_wrap=True)
    table.add_column("$cost", justify="right", no_wrap=True)
    table.add_column("tokens", justify="right", no_wrap=True)
    table.add_column("steps", justify="right", no_wrap=True)
    table.add_column("time", justify="right", no_wrap=True)
    for row in cohort.summary_rows():
        order = row["finished_order"]
        outcome = row["terminal_reason"] or row["liveness"]
        if order == 1:
            style: str | None = "bold green"
        elif row["liveness"] == "error":
            style = "red"
        else:
            style = None
        table.add_row(
            str(order) if order else "—",
            row["label"],
            row["model"],
            outcome,
            f"{row['cost']:.4f}",
            str(row["tokens_in"] + row["tokens_out"]),
            str(row["steps"]),
            f"{row['elapsed']:.1f}s",
            style=style,
        )
    return table


def _spans_text(
    marker: str, spans: list[Span], base_style: str, word_style: str,
) -> Text:
    """Render one diff line from its word spans (changed tokens highlighted)."""
    text = Text()
    text.append(marker, style=base_style)
    for span in spans:
        text.append(span.text, style=word_style if span.changed else base_style)
    return text


def word_diff_lines(
    removes: list[str], adds: list[str], *, palette: Palette | None = None,
) -> list[Text] | None:
    """Render one removal/addition run with word-level highlights (R-REN-10).

    Args:
        removes: Consecutive removed lines, markers stripped.
        adds: The consecutive added lines that follow them.
        palette: Theme palette supplying the ``diff.*`` slots.

    Returns:
        The rendered lines (removals first, then additions, as in a unified
        diff), or ``None`` when the run cannot be paired honestly — unbalanced
        run lengths, or every pair too dissimilar to word-diff — so the caller
        falls back to plain line colors.
    """
    pairs = pair_runs(removes, adds)
    if pairs is None:
        return None
    diffed = [word_spans(old, new) for old, new in pairs]
    if all(entry is None for entry in diffed):
        return None
    pal = palette if palette is not None else _DEFAULT_PALETTE
    remove_style, add_style = pal.style("diff.remove"), pal.style("diff.add")
    removed: list[Text] = []
    added: list[Text] = []
    for (old, new), entry in zip(pairs, diffed):
        if entry is None:
            removed.append(Text(f"-{old}", style=remove_style))
            added.append(Text(f"+{new}", style=add_style))
            continue
        old_spans, new_spans = entry
        removed.append(_spans_text(
            "-", old_spans, remove_style, pal.style("diff.remove-word"),
        ))
        added.append(_spans_text(
            "+", new_spans, add_style, pal.style("diff.add-word"),
        ))
    return removed + added


def render_diff(diff: str, *, palette: Palette | None = None) -> list[Text]:
    """Colorize a unified diff into per-line renderables (add/remove/hunk).

    Removal/addition runs that pair 1↔1 are word-diffed and only the changed
    tokens are inverse-highlighted (R-REN-10); indentation is never
    highlighted, and a pair that is not really an edit of its partner falls
    back to plain line color.

    Args:
        diff: A unified diff (one file or many).
        palette: Theme palette supplying the ``diff.*`` slots; ``None`` uses
            the default theme, whose values are the previously hardcoded ones.

    Returns:
        One renderable per diff line, in order.
    """
    pal = palette if palette is not None else _DEFAULT_PALETTE
    out: list[Text] = []
    removes: list[str] = []
    adds: list[str] = []

    def flush() -> None:
        if not removes and not adds:
            return
        paired = word_diff_lines(removes, adds, palette=pal)
        if paired is not None:
            out.extend(paired)
        else:
            out.extend(Text(f"-{line}", style=pal.style("diff.remove")) for line in removes)
            out.extend(Text(f"+{line}", style=pal.style("diff.add")) for line in adds)
        removes.clear()
        adds.clear()

    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith(("diff ", "index ")):
            flush()
            out.append(Text(line, style=pal.style("diff.meta")))
        elif line.startswith("+"):
            adds.append(line[1:])
        elif line.startswith("-"):
            if adds:  # a new run starts once additions have been seen
                flush()
            removes.append(line[1:])
        elif line.startswith("@@"):
            flush()
            out.append(Text(line, style=pal.style("diff.hunk")))
        else:
            flush()
            out.append(Text(line, style=pal.style("diff.context")))
    flush()
    return out


class ResultsScreen(Screen):
    """Full-screen cohort comparison: scoreboard + per-lane diff viewer."""

    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("q", "close", "Back"),
        Binding("tab", "next_lane", "Next lane"),
        Binding("shift+tab", "prev_lane", "Prev lane"),
        Binding("right", "next_lane", "Next"),
        Binding("left", "prev_lane", "Prev"),
        Binding("n", "next_file", "Next file"),
        Binding("p", "prev_file", "Prev file"),
        Binding("s", "toggle_split", "Split/unified"),
    ]

    CSS = """
    #results-title { height: 1; background: $primary; color: $text; padding: 0 1; }
    #scoreboard { height: auto; max-height: 50%; padding: 0 1; }
    #diff-header { height: 1; background: $boost; color: $text; padding: 0 1; }
    #diff-body { height: 1fr; padding: 0 1; }
    """

    def __init__(
        self, cohort: Cohort, *, palette: Palette | None = None, **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._cohort = cohort
        self._idx = 0
        self._file_idx = 0
        self._split = False
        #: Theme palette for the diff slots (R-THEME-1); the app passes its
        #: live one so the comparison screen follows ``/theme``.
        self._palette = palette if palette is not None else _DEFAULT_PALETTE

    def compose(self) -> ComposeResult:
        task = self._cohort.task or "—"
        if len(task) > 60:
            task = task[:59] + "…"
        yield Static(
            Text(f" Cohort results · {len(self._cohort.lanes)} lanes · task: {task}", style="bold"),
            id="results-title",
        )
        yield Static(scoreboard_table(self._cohort), id="scoreboard")
        yield Static("", id="diff-header")
        # auto_scroll off: a diff is read top-down — with it on, every rebuilt
        # view (lane/file/split switch) landed scrolled to the BOTTOM.
        yield RichLog(id="diff-body", wrap=False, markup=False, highlight=False,
                      auto_scroll=False)
        yield Footer()

    def on_mount(self) -> None:
        self._show_lane()

    def _lane_diff(self, lane: Any) -> str:
        if lane.workspace is None:
            return ""
        try:
            return str(lane.workspace.diff())
        except Exception as exc:  # noqa: BLE001 - diff is best-effort
            return f"(diff unavailable: {exc})"

    def _show_lane(self) -> None:
        lanes = self._cohort.lanes
        if not lanes:
            return
        self._idx %= len(lanes)
        lane = lanes[self._idx]
        t = lane.telemetry
        diff = self._lane_diff(lane)
        files = split_diff_files(diff) if diff.strip() else []
        if files:
            self._file_idx %= len(files)
        else:
            self._file_idx = 0
        file_part = ""
        if files:
            fname = files[self._file_idx][0]
            file_part = f" · file {self._file_idx + 1}/{len(files)}: {fname} · [{'split' if self._split else 'unified'}]"
        self.query_one("#diff-header", Static).update(Text.assemble(
            (f" diff · lane {self._idx + 1}/{len(lanes)} · ", "bold"),
            (f"{lane.label} ({lane.config.model})", "bold cyan"),
            (f"  ·  ${t.cost:.4f} · {t.steps} steps · {t.terminal_reason or t.liveness.value}", "dim"),
            (file_part, "dim"),
        ))
        log = self.query_one("#diff-body", RichLog)
        log.clear()
        log.scroll_home(animate=False)
        if not files:
            log.write(Text("(no changes produced by this lane)", style="dim italic"))
            return
        _, file_diff = files[self._file_idx]
        if self._split:
            self._write_split(log, file_diff)
        else:
            for line in render_diff(file_diff, palette=self._palette):
                log.write(line)

    def _write_split(self, log: RichLog, file_diff: str) -> None:
        """Side-by-side rendering: old text left, new text right (§13.8).

        Paired change rows carry the same word-level highlights as the unified
        view (R-REN-10) — each side is built as spans, then truncated/padded to
        the column so the highlight survives the fit.
        """
        pal = self._palette
        width = max((self.size.width or 120) // 2 - 3, 20)

        def clip(text: str) -> str:
            return text[: width - 1] + "…" if len(text) > width else text.ljust(width)

        def cell(spans: list[Span] | None, raw: str, base: str, word: str) -> Text:
            text = (
                _spans_text("", spans, base, word) if spans is not None
                else Text(raw, style=base if raw else "")
            )
            text.truncate(width, overflow="ellipsis", pad=True)
            return text

        for kind, left, right in split_rows(file_diff):
            if kind == "meta":
                style = pal.style("diff.hunk") if left.startswith("@@") else pal.style("diff.meta")
                log.write(Text(left, style=style))
            elif kind == "ctx":
                log.write(Text(f"{clip(left)} │ {clip(right)}", style=pal.style("diff.context")))
            else:
                entry = word_spans(left, right)
                old_spans, new_spans = entry if entry is not None else (None, None)
                row = cell(
                    old_spans, left, pal.style("diff.remove"), pal.style("diff.remove-word"),
                )
                row.append(" │ ", style=pal.style("base.dim"))
                row.append_text(cell(
                    new_spans, right, pal.style("diff.add"), pal.style("diff.add-word"),
                ))
                log.write(row)

    def action_next_lane(self) -> None:
        self._idx += 1
        self._file_idx = 0
        self._show_lane()

    def action_prev_lane(self) -> None:
        self._idx -= 1
        self._file_idx = 0
        self._show_lane()

    def action_next_file(self) -> None:
        self._file_idx += 1
        self._show_lane()

    def action_prev_file(self) -> None:
        self._file_idx -= 1
        self._show_lane()

    def action_toggle_split(self) -> None:
        self._split = not self._split
        self._show_lane()

    def action_close(self) -> None:
        self.app.pop_screen()

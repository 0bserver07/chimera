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

if TYPE_CHECKING:
    from chimera.tui.cohort import Cohort

__all__ = ["ResultsScreen", "scoreboard_table", "render_diff"]


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


def render_diff(diff: str) -> list[Text]:
    """Colorize a unified diff into per-line renderables (add/remove/hunk)."""
    out: list[Text] = []
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith(("diff ", "index ")):
            out.append(Text(line, style="bold"))
        elif line.startswith("+"):
            out.append(Text(line, style="green"))
        elif line.startswith("-"):
            out.append(Text(line, style="red"))
        elif line.startswith("@@"):
            out.append(Text(line, style="cyan"))
        else:
            out.append(Text(line))
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
    ]

    CSS = """
    #results-title { height: 1; background: $primary; color: $text; padding: 0 1; }
    #scoreboard { height: auto; max-height: 50%; padding: 0 1; }
    #diff-header { height: 1; background: $boost; color: $text; padding: 0 1; }
    #diff-body { height: 1fr; padding: 0 1; }
    """

    def __init__(self, cohort: Cohort, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cohort = cohort
        self._idx = 0

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
        yield RichLog(id="diff-body", wrap=False, markup=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self._show_lane()

    def _show_lane(self) -> None:
        lanes = self._cohort.lanes
        if not lanes:
            return
        self._idx %= len(lanes)
        lane = lanes[self._idx]
        t = lane.telemetry
        self.query_one("#diff-header", Static).update(Text.assemble(
            (f" diff · lane {self._idx + 1}/{len(lanes)} · ", "bold"),
            (f"{lane.label} ({lane.config.model})", "bold cyan"),
            (f"  ·  ${t.cost:.4f} · {t.steps} steps · {t.terminal_reason or t.liveness.value}", "dim"),
        ))
        log = self.query_one("#diff-body", RichLog)
        log.clear()
        diff = ""
        if lane.workspace is not None:
            try:
                diff = lane.workspace.diff()
            except Exception as exc:  # noqa: BLE001 - diff is best-effort
                diff = f"(diff unavailable: {exc})"
        if diff.strip():
            for line in render_diff(diff):
                log.write(line)
        else:
            log.write(Text("(no changes produced by this lane)", style="dim italic"))

    def action_next_lane(self) -> None:
        self._idx += 1
        self._show_lane()

    def action_prev_lane(self) -> None:
        self._idx -= 1
        self._show_lane()

    def action_close(self) -> None:
        self.app.pop_screen()

"""Chimera TUI (Phase 1) — a single-agent coding TUI over ``AgentDriver``.

The app owns no agent state: it drives :meth:`AgentDriver.send` (a stream of
``LoopEvent``s) and renders each event into a transcript. Assistant text is
accumulated and committed when the turn's message completes; tool calls and
results stream in as they happen (the live feel). ``Ctrl+C`` cancels the
running turn, slash commands are handled locally.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from rich.text import Text
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import Footer, Header, Input, RichLog, Static
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The Chimera TUI needs the 'tui' extra:\n"
        "  pip install 'chimera-run[tui]'   (or: pip install textual)"
    ) from exc

from chimera.core.loop_events import LoopEventType

if TYPE_CHECKING:
    from chimera.assembly.driver import AgentDriver

__all__ = ["ChimeraTUI", "run_tui"]


class ChimeraTUI(App):
    """A single-agent coding TUI bound to one :class:`AgentDriver`."""

    CSS = """
    Screen { layers: base; }
    #status { height: 1; background: $primary; color: $text; padding: 0 1; }
    #transcript { height: 1fr; padding: 0 1; }
    #prompt { dock: bottom; border: round $secondary; }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel / quit", priority=True),
        Binding("ctrl+d", "quit", "Quit"),
        Binding("ctrl+l", "clear_convo", "Clear"),
    ]

    def __init__(self, driver: AgentDriver, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._agent = driver
        self._chunks: list[str] = []
        self._turn_active = False

    # -- layout ---------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(self._status_text(), id="status")
        yield RichLog(id="transcript", wrap=True, markup=False, highlight=False)
        yield Input(placeholder="Ask, or /help …", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self._log(Text(
            f"Chimera TUI — {self._agent.model} — {self._fmt_ctx()} ctx.  "
            f"/help for commands · Ctrl+C cancels a turn.",
            style="dim",
        ))

    # -- helpers --------------------------------------------------------
    def _log(self, renderable: Any) -> None:
        self.query_one("#transcript", RichLog).write(renderable)

    def _fmt_ctx(self) -> str:
        c = self._agent.context_window
        return f"{c:,}" if c else "?"

    def _status_text(self) -> str:
        state = "running" if self._turn_active else "idle"
        return (f" {self._agent.model}  ·  {len(self._agent.tools)} tools  ·  "
                f"${self._agent.total_cost:.4f}  ·  {state}")

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self._status_text())

    # -- input ----------------------------------------------------------
    @on(Input.Submitted, "#prompt")
    def _on_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self._handle_command(text)
            return
        if self._turn_active:
            # Mid-run: steer the running turn instead of starting a new one.
            self._agent.steer(text)
            self._log(Text.assemble(("↳ steer: ", "magenta"), (text, "magenta")))
            return
        self._log(Text.assemble(("› ", "bold cyan"), (text, "bold")))
        self._run_turn(text)

    def _handle_command(self, text: str) -> None:
        cmd = text.split()[0]
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/help":
            self._log(Text(
                "/help /model /cost /tools /clear /exit   ·   "
                "Ctrl+C cancel · Ctrl+L clear · type while running to steer",
                style="dim",
            ))
        elif cmd == "/model":
            self._log(Text(f"{self._agent.model}  ({self._fmt_ctx()} ctx)", style="dim"))
        elif cmd == "/cost":
            self._log(Text(f"cumulative: ${self._agent.total_cost:.4f}", style="dim"))
        elif cmd == "/tools":
            names = ", ".join(t.name for t in self._agent.tools)
            self._log(Text(names or "(none)", style="dim"))
        elif cmd == "/clear":
            self._agent.clear()
            self.query_one("#transcript", RichLog).clear()
            self._log(Text("(conversation cleared)", style="dim"))
        else:
            self._log(Text(f"unknown command: {cmd}", style="red"))

    # -- the agent turn (async worker) ---------------------------------
    @work(exclusive=True)
    async def _run_turn(self, text: str) -> None:
        self._turn_active = True
        self._refresh_status()
        self._chunks = []
        try:
            async for ev in self._agent.send(text):
                self._render_event(ev)
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            self._log(Text(f"turn failed: {exc}", style="red"))
        finally:
            self._commit_assistant()
            self._turn_active = False
            self._refresh_status()

    def _render_event(self, ev: Any) -> None:
        t = ev.type
        if t == LoopEventType.assistant_chunk:
            self._chunks.append(str(ev.data))
        elif t == LoopEventType.assistant:
            if self._chunks:
                self._commit_assistant()
            else:
                content = getattr(ev.data, "content", "") or ""
                if content.strip():
                    self._log(Text(content))
        elif t == LoopEventType.tool_use:
            tc = ev.data
            args = getattr(tc, "arguments", {}) or {}
            preview = ", ".join(f"{k}={_short(v)}" for k, v in list(args.items())[:3])
            self._log(Text.assemble(
                ("⚙ ", "yellow"), (getattr(tc, "name", "?"), "bold yellow"),
                (f"({preview})", "dim"),
            ))
        elif t == LoopEventType.tool_result:
            tc, result = ev.data if isinstance(ev.data, tuple) else (None, ev.data)
            out = (getattr(result, "output", "") or "").rstrip()
            if out:
                if len(out) > 1500:
                    out = out[:800] + "\n… [truncated] …\n" + out[-500:]
                ok = getattr(result, "success", True)
                self._log(Text(out, style="green" if ok else "red"))
        elif t == LoopEventType.error:
            self._log(Text(f"error: {ev.data}", style="red"))
        elif t == LoopEventType.result:
            r = ev.data
            self._log(Text(
                f"· {getattr(r, 'turn_count', 0)} steps · "
                f"${getattr(r, 'cost_usd', 0) or 0:.4f} · ${self._agent.total_cost:.4f} total",
                style="dim",
            ))
        elif t == LoopEventType.system and ev.data:
            self._log(Text(str(ev.data), style="dim"))

    def _commit_assistant(self) -> None:
        if self._chunks:
            self._log(Text("".join(self._chunks)))
            self._chunks = []

    # -- actions --------------------------------------------------------
    def action_cancel(self) -> None:
        if self._turn_active:
            self._agent.cancel()
            self._log(Text("· cancel requested", style="red"))
        else:
            self.exit()

    def action_clear_convo(self) -> None:
        self._agent.clear()
        self.query_one("#transcript", RichLog).clear()


def _short(value: Any, limit: int = 40) -> str:
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def run_tui(
    model: str = "glm-5.2",
    project_dir: str | None = None,
    preset: str = "coding_agent",
    **driver_kwargs: Any,
) -> None:
    """Launch the single-agent Chimera TUI."""
    from chimera.assembly.driver import AgentDriver

    driver = AgentDriver(
        model=model, project_dir=project_dir, preset=preset, **driver_kwargs,
    )
    ChimeraTUI(driver).run()

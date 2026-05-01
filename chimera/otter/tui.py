"""Otter TUI prototype — a richer interactive frontend for the otter agent.

Today's otter REPL is readline-based (see :mod:`chimera.otter.repl`).
This module ships a *prototype-grade* alternative built on
`textual <https://textualize.io/>`_ that talks to an in-process
:class:`chimera.otter.server.OtterServer` over the same HTTP + SSE
surface a remote IDE / web client would use. The TUI is therefore a
thin presentation layer: it owns no agent state, no tool dispatch, no
provider plumbing — only widgets + input handling.

The split keeps three things clean:

* **Optional dependency.** ``textual`` is gated behind the ``[tui]``
  extra; importing this module without it fails fast with a friendly
  ``ImportError`` carrying the install hint.
* **No code duplication.** Every transport semantic (turn dispatch,
  cancellation, event fan-out, permission gating) lives in
  :class:`OtterServer`. Wiring a second client (a future web UI, an
  IDE plugin, etc.) reuses the same surface.
* **Testability.** :func:`build_app` returns a ``textual.App`` instance
  bound to a stub or live :class:`OtterServer`; tests use textual's
  ``App.run_test()`` harness to drive interactions without ever opening
  a real socket.

Layout
------

::

    +------------------------------------------------------------+
    | model: claude-sonnet-4-6   session: ab12cd  cost: $0.0042  |  <- StatusBar (top)
    +-----------------------------------------+------------------+
    |                                         |                  |
    |  user: hello                            |  tool calls:     |
    |  agent: hi! what can I do for you?      |   - bash (ok)    |
    |  ...                                    |   - read_file    |
    |  (Conversation, RichLog)                |  (Side panel)    |
    |                                         |                  |
    +-----------------------------------------+------------------+
    | > _                                                        |  <- Input (bottom)
    +------------------------------------------------------------+

Key bindings
------------

* ``ctrl+c`` — cancel the in-flight turn (sends ``POST /session/<id>/cancel``).
* ``ctrl+d`` — quit the app.
* ``f1``     — show / hide a help banner.
* ``f2``     — toggle the right-side tool-call panel.

Trademark hygiene: the TUI never names the upstream open-source coding
agent; copy stays neutral ("Otter", "Chimera").
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.otter.server import OtterServer, OtterSessionState


__all__ = [
    "TUIConfig",
    "build_app",
    "run_tui",
]
# WHY: ``OtterTUIApp`` is exposed lazily via ``__getattr__`` so importing
# this module without ``textual`` installed stays cheap. Adding it to
# ``__all__`` would force ruff's F822 unresolved-name check to trip; we
# document the dynamic export with this comment instead.


_INSTALL_HINT = (
    "The otter TUI requires the optional 'textual' dependency. "
    "Install it with:\n\n    pip install 'chimera-run[tui]'\n\n"
    "or, with uv:\n\n    uv sync --extra tui\n"
)


def _require_textual() -> Any:
    """Import :mod:`textual` lazily and raise a friendly error if missing.

    The TUI module is loaded only on the ``--tui`` code path; users on
    the readline REPL never pay the import cost. We re-raise as a
    :class:`ImportError` so the CLI dispatcher can map it onto a
    non-zero exit + a one-line stderr hint.

    Returns:
        The imported ``textual`` module.

    Raises:
        ImportError: When ``textual`` is not installed.
    """
    try:
        import textual  # noqa: F401 - presence check only
    except ImportError as exc:  # pragma: no cover - exercised via the CLI
        raise ImportError(_INSTALL_HINT) from exc
    return textual


@dataclass
class TUIConfig:
    """Tunables for :class:`OtterTUIApp`.

    Attributes:
        model: Display model name shown in the status bar. The TUI
            itself is provider-agnostic; the value is informational.
        working_dir: Working directory passed to
            :meth:`OtterServer.create_session`. Empty string keeps the
            session at the server's default cwd.
        session_id: Optional pre-created session id. When ``None`` the
            app calls :meth:`OtterServer.create_session` on mount.
        title: Window title.
        cancel_timeout_s: Maximum seconds to wait for a cancellation
            request to fan out before falling back to a local "stop
            listening" — defensive only, since
            :meth:`OtterServer.cancel_session` is non-blocking.
    """

    model: str = "claude-sonnet-4-6"
    working_dir: str = ""
    session_id: str | None = None
    title: str = "Otter"
    cancel_timeout_s: float = 2.0


@dataclass
class _SessionRuntime:
    """Live runtime state the app threads through its widgets.

    The TUI subscribes to the server's SSE stream via
    :meth:`OtterServer.subscribe`; the subscriber thread pushes raw
    envelope dicts onto :attr:`event_queue`. The app's mounted message
    pump pulls from the queue and fans out to widgets on the main
    thread (textual's update calls are not thread-safe).
    """

    server: "OtterServer"
    state: "OtterSessionState"
    cost_usd: float = 0.0
    pending_message_id: str | None = None
    cancel_subscriber: Callable[[], None] | None = None
    tool_call_count: int = 0
    error_count: int = 0
    seen_event_ids: set[str] = field(default_factory=set)


def build_app(server: "OtterServer", config: TUIConfig | None = None) -> Any:
    """Construct an :class:`OtterTUIApp` bound to *server*.

    Tests use this entry point so they can drive the app via
    ``App.run_test()`` against a stub or fake :class:`OtterServer`.

    Args:
        server: The in-process HTTP server the TUI talks to. The TUI
            never assumes the server's listener is bound — every call
            goes through the in-process Python API
            (:meth:`OtterServer.create_session`,
            :meth:`OtterServer.submit_message`,
            :meth:`OtterServer.subscribe`,
            :meth:`OtterServer.cancel_session`). That keeps the TUI
            usable even when no socket has been started.
        config: Optional tunables. Defaults to a fresh
            :class:`TUIConfig`.

    Returns:
        An ``OtterTUIApp`` instance ready for ``app.run()`` or
        ``app.run_test()``.

    Raises:
        ImportError: When ``textual`` is not installed.
    """
    _require_textual()
    cls = _build_app_class()
    globals()["OtterTUIApp"] = cls
    return cls(server=server, config=config or TUIConfig())


def run_tui(server: "OtterServer", config: TUIConfig | None = None) -> int:
    """Launch the TUI synchronously and return a process exit code.

    Args:
        server: A live :class:`OtterServer`.
        config: Optional tunables.

    Returns:
        ``0`` on graceful quit (Ctrl-D / window close), ``130`` on
        Ctrl-C interrupt.
    """
    app = build_app(server, config)
    try:
        app.run()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        return 130
    return 0


# ---------------------------------------------------------------------------
# Textual app — defined lazily so the import cost is only paid when used.
# ---------------------------------------------------------------------------


def _build_app_class() -> type:
    """Return :class:`OtterTUIApp` lazily.

    Defining the class inside a factory means the textual import only
    happens when a caller actually constructs an app — modules that
    just touch :class:`TUIConfig` (e.g. a future preset registry) don't
    pull textual in.
    """
    _require_textual()

    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, Input, RichLog, Static

    class _OtterTUIApp(App):  # type: ignore[misc, no-any-unimported, type-arg]
        """Minimal otter TUI bound to a live :class:`OtterServer`.

        The app is intentionally thin: every meaningful state
        transition (new turn, cancel, tool call) routes through the
        server's public methods. Widgets only render — they never
        touch agent / provider plumbing directly.
        """

        CSS = """
        Screen { layout: vertical; }
        #status_bar { dock: top; height: 1; background: $primary; color: $text; padding: 0 1; }
        #body { height: 1fr; }
        #conversation { width: 2fr; border: round $primary; padding: 0 1; }
        #side_panel { width: 1fr; border: round $secondary; padding: 0 1; }
        #side_panel.hidden { display: none; }
        #input { dock: bottom; height: 3; }
        #help_banner { dock: top; height: 3; background: $warning; color: $text; padding: 0 1; }
        #help_banner.hidden { display: none; }
        """

        BINDINGS = [
            Binding("ctrl+c", "cancel_turn", "Cancel turn", show=True),
            Binding("ctrl+d", "quit", "Quit", show=True),
            Binding("f1", "toggle_help", "Help", show=True),
            Binding("f2", "toggle_side_panel", "Side panel", show=True),
        ]

        def __init__(
            self,
            *,
            server: "OtterServer",
            config: TUIConfig,
        ) -> None:
            super().__init__()
            self._server = server
            self._config = config
            self._runtime: _SessionRuntime | None = None
            # Widgets are bound on mount so __init__ stays cheap and
            # tests can inspect ``app._config`` before run_test() pumps.
            self._conversation: RichLog | None = None
            self._side_panel: RichLog | None = None
            self._status_bar: Static | None = None
            self._help_banner: Static | None = None
            self._input: Input | None = None

        # ------------------------------------------------------------------
        # Layout
        # ------------------------------------------------------------------

        def compose(self) -> ComposeResult:
            self.title = self._config.title
            yield Header(show_clock=False)
            yield Static(self._render_status(), id="status_bar")
            yield Static(
                "Otter TUI — F1 help, F2 toggles side panel, "
                "Ctrl+C cancels a turn, Ctrl+D quits.",
                id="help_banner",
                classes="hidden",
            )
            with Horizontal(id="body"):
                with Vertical():
                    yield RichLog(
                        id="conversation",
                        highlight=True,
                        markup=True,
                        wrap=True,
                    )
                with Vertical(id="side_panel"):
                    yield RichLog(
                        id="side_panel_log",
                        highlight=True,
                        markup=True,
                        wrap=True,
                    )
            yield Input(placeholder="Type a message and press Enter…", id="input")
            yield Footer()

        # ------------------------------------------------------------------
        # Lifecycle
        # ------------------------------------------------------------------

        def on_mount(self) -> None:
            """Bind widget refs, create a server session, start the SSE pump."""
            self._conversation = self.query_one("#conversation", RichLog)
            self._side_panel = self.query_one("#side_panel_log", RichLog)
            self._status_bar = self.query_one("#status_bar", Static)
            self._help_banner = self.query_one("#help_banner", Static)
            self._input = self.query_one("#input", Input)

            state = self._resolve_session()
            self._runtime = _SessionRuntime(server=self._server, state=state)
            self._refresh_status()
            self._log_conversation(
                f"[bold]Session ready[/bold] (id={state.session_id[:8]}…)."
            )
            self._start_event_pump()
            if self._input is not None:
                self._input.focus()

        def on_unmount(self) -> None:
            """Detach the SSE subscriber when the app is torn down."""
            runtime = self._runtime
            if runtime is None or runtime.cancel_subscriber is None:
                return
            try:
                runtime.cancel_subscriber()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

        # ------------------------------------------------------------------
        # Event pump (server SSE -> textual messages)
        # ------------------------------------------------------------------

        def _resolve_session(self) -> "OtterSessionState":
            """Pick up an existing session id or create a fresh one."""
            if self._config.session_id is not None:
                existing = self._server.get_session(self._config.session_id)
                if existing is not None:
                    return existing
            return self._server.create_session(
                working_dir=self._config.working_dir
            )

        def _start_event_pump(self) -> None:
            """Spawn a daemon thread that drains SSE envelopes onto the UI.

            The pump uses :meth:`App.call_from_thread` so widget updates
            happen on textual's event loop. The thread exits when
            :meth:`OtterServer.unsubscribe` puts a sentinel ``None`` on
            the queue (server shutdown) or when the app is unmounted.
            """
            runtime = self._runtime
            assert runtime is not None  # mounted already
            queue_obj = self._server.subscribe(runtime.state)
            stop = threading.Event()

            def _cancel() -> None:
                stop.set()
                # Wake the pump so it sees ``stop``.
                try:
                    queue_obj.put_nowait(None)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self._server.unsubscribe(runtime.state, queue_obj)
                except Exception:  # noqa: BLE001
                    pass

            runtime.cancel_subscriber = _cancel

            def _pump() -> None:
                while not stop.is_set():
                    try:
                        envelope = queue_obj.get(timeout=0.5)
                    except Exception:  # noqa: BLE001 - queue.Empty etc.
                        continue
                    if envelope is None:
                        return
                    try:
                        self.call_from_thread(self._handle_envelope, envelope)
                    except Exception:  # noqa: BLE001 - app may be shutting down
                        return

            thread = threading.Thread(target=_pump, daemon=True)
            thread.start()

        def _handle_envelope(self, envelope: dict[str, Any]) -> None:
            """Render one SSE envelope onto the conversation / side panel.

            Envelope shape mirrors :meth:`OtterServer.emit_event`:
            ``{"id", "event", "data", "timestamp"}``.
            """
            runtime = self._runtime
            if runtime is None:
                return
            ev_id = str(envelope.get("id", ""))
            if ev_id and ev_id in runtime.seen_event_ids:
                return
            if ev_id:
                runtime.seen_event_ids.add(ev_id)

            ev_type = str(envelope.get("event", ""))
            data = envelope.get("data") or {}
            if not isinstance(data, dict):
                data = {"value": data}

            if ev_type == "user_message":
                text = str(data.get("text", ""))
                self._log_conversation(f"[bold cyan]you[/bold cyan]> {text}")
            elif ev_type == "loop_event":
                self._render_loop_event(data)
            elif ev_type == "result":
                output = str(data.get("output", "") or "")
                cost = float(data.get("cost", 0.0) or 0.0)
                runtime.cost_usd += cost
                runtime.pending_message_id = None
                if output:
                    self._log_conversation(f"[bold green]otter[/bold green]> {output}")
                if data.get("cancelled"):
                    self._log_conversation("[yellow](cancelled)[/yellow]")
                self._refresh_status()
            elif ev_type == "error":
                runtime.error_count += 1
                runtime.pending_message_id = None
                msg = str(data.get("error", "unknown error"))
                self._log_conversation(f"[bold red]error[/bold red]: {msg}")
                self._refresh_status()
            elif ev_type == "permission_request":
                pid = str(data.get("permission_id", ""))
                self._log_side(f"[bold yellow]permission requested[/bold yellow]: {pid[:8]}")
            else:
                # Unknown event types still surface on the side panel so
                # the user has *some* signal that work is happening.
                self._log_side(f"{ev_type}: {_compact_json(data)}")

        def _render_loop_event(self, data: dict[str, Any]) -> None:
            """Format one inner ``LoopEvent`` payload onto the side panel.

            The server flattens ``LoopEvent`` into ``{type, data, turn,
            timestamp}`` already; we cherry-pick the fields a human
            cares about (text deltas, tool calls, costs) and route them
            to the conversation or side panel accordingly.
            """
            runtime = self._runtime
            assert runtime is not None
            inner_type = str(data.get("type", ""))
            inner = data.get("data") or {}
            if not isinstance(inner, dict):
                inner = {"value": inner}

            if inner_type in {"assistant", "assistant_chunk", "text_delta"}:
                text = str(inner.get("text", ""))
                if text:
                    self._log_conversation(f"[green]otter[/green]> {text}")
            elif inner_type in {"tool_use", "tool_call"}:
                runtime.tool_call_count += 1
                name = str(inner.get("name", "?"))
                self._log_side(f"[cyan]tool[/cyan] {name}")
            elif inner_type in {"tool_result"}:
                name = str(inner.get("name", "?"))
                ok = inner.get("success", True)
                marker = "[green]ok[/green]" if ok else "[red]fail[/red]"
                self._log_side(f"  -> {name} {marker}")
            else:
                # Fall through: still show *something* on the side panel.
                self._log_side(f"{inner_type}: {_compact_json(inner)}")
            self._refresh_status()

        # ------------------------------------------------------------------
        # User input
        # ------------------------------------------------------------------

        def on_input_submitted(self, event: Input.Submitted) -> None:
            """Forward the submitted text into the server as a new turn."""
            text = (event.value or "").strip()
            if not text:
                return
            if self._input is not None:
                self._input.value = ""
            runtime = self._runtime
            if runtime is None:
                self._log_conversation("[red]not yet ready[/red]")
                return
            try:
                message_id = self._server.submit_message(runtime.state, text)
            except Exception as exc:  # noqa: BLE001
                self._log_conversation(f"[red]submit failed[/red]: {exc}")
                return
            runtime.pending_message_id = message_id
            self._refresh_status()

        # ------------------------------------------------------------------
        # Actions
        # ------------------------------------------------------------------

        def action_cancel_turn(self) -> None:
            """Send a cancel for the in-flight turn, if any."""
            runtime = self._runtime
            if runtime is None or runtime.pending_message_id is None:
                self._log_conversation("[yellow](no turn to cancel)[/yellow]")
                return
            try:
                ok = self._server.cancel_session(runtime.state.session_id)
            except Exception as exc:  # noqa: BLE001
                self._log_conversation(f"[red]cancel failed[/red]: {exc}")
                return
            self._log_conversation(
                "[yellow](cancel requested)[/yellow]"
                if ok
                else "[yellow](cancel target missing)[/yellow]"
            )

        def action_toggle_help(self) -> None:
            """Show / hide the help banner."""
            banner = self._help_banner
            if banner is None:
                return
            if "hidden" in banner.classes:
                banner.remove_class("hidden")
            else:
                banner.add_class("hidden")

        def action_toggle_side_panel(self) -> None:
            """Show / hide the side panel."""
            panel = self.query_one("#side_panel")
            if "hidden" in panel.classes:
                panel.remove_class("hidden")
            else:
                panel.add_class("hidden")

        # ------------------------------------------------------------------
        # Rendering helpers
        # ------------------------------------------------------------------

        def _render_status(self) -> str:
            runtime = self._runtime
            sid = (
                runtime.state.session_id[:8]
                if runtime is not None
                else "(pending)"
            )
            cost = runtime.cost_usd if runtime is not None else 0.0
            tools = runtime.tool_call_count if runtime is not None else 0
            errors = runtime.error_count if runtime is not None else 0
            return (
                f"model: {self._config.model}  "
                f"session: {sid}  "
                f"cost: ${cost:.4f}  "
                f"tools: {tools}  "
                f"errors: {errors}"
            )

        def _refresh_status(self) -> None:
            if self._status_bar is not None:
                self._status_bar.update(self._render_status())

        def _log_conversation(self, line: str) -> None:
            if self._conversation is not None:
                self._conversation.write(line)

        def _log_side(self, line: str) -> None:
            if self._side_panel is not None:
                self._side_panel.write(line)

    return _OtterTUIApp


def _compact_json(data: Any) -> str:
    """Return a compact JSON repr of *data* (best-effort, never raises)."""
    try:
        return json.dumps(data, default=str, ensure_ascii=False)[:120]
    except Exception:  # noqa: BLE001
        return repr(data)[:120]


def __getattr__(name: str) -> Any:
    """Lazy attribute access — build :class:`OtterTUIApp` only on demand.

    Importing :mod:`chimera.otter.tui` itself stays cheap (and safe
    when textual is missing — only the explicit ``OtterTUIApp`` lookup
    triggers the import). Other names raise the usual
    :class:`AttributeError`.
    """
    if name == "OtterTUIApp":
        cls = _build_app_class()
        globals()[name] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

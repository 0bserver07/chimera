"""Otter HTTP server: REST + SSE bridge to a Chimera agent session.

Where :mod:`chimera.env.remote` is the *client* of an HTTP workspace
server, this module is the *server* — it exposes an in-process
:class:`chimera.core.agent.Agent` over a small REST + SSE surface so any
TUI / IDE / web client can drive the same session over the wire.

Trademark hygiene: this module never names the upstream open-source
coding agent in user-visible source.

API surface
-----------

================================ ====== ==========================================
Path                              Method Purpose
================================ ====== ==========================================
``/healthz``                      GET    Liveness probe (returns ``{"status":"ok"}``).
``/session``                      POST   Create a new session. Body may include
                                         ``{"working_dir": "..."}``. Returns
                                         ``{"session_id": "..."}``.
``/session``                      GET    List session ids.
``/session/<id>``                 GET    Return session state snapshot.
``/session/<id>/message``         POST   Send a user prompt
                                         (``{"text": "..."}``). Returns the
                                         message id; agent runs in the
                                         background and emits SSE events.
``/session/<id>/events``          GET    Server-Sent Events stream of
                                         :class:`~chimera.core.loop_events.LoopEvent`
                                         payloads. Supports
                                         ``Last-Event-ID`` for resume.
``/session/<id>/cancel``          POST   Cooperatively cancel an in-flight
                                         agent run for that session. Returns
                                         ``204 No Content``.
``/tool/approve``                 POST   Resolve a pending permission request
                                         (``{"permission_id": "...",``
                                         ``"approved": true}``).
``/commands``                     GET    List custom slash commands discovered
                                         from ``.opencode/command/*.md`` in the
                                         server's commands cwd.
``/commands/<name>/invoke``       POST   Render a custom command template and
                                         push it as a message into the active
                                         session. Body:
                                         ``{"session_id": "...",``
                                         ``"args": [...], "kwargs": {...}}``.
================================ ====== ==========================================

Auth
----

Pass ``--auth-token <SECRET>`` to require ``Authorization: Bearer <SECRET>``
on every request except ``/healthz``. Without ``--auth-token`` the server
is open (intended for ``127.0.0.1`` local use).

Implementation notes
--------------------

* **Stdlib only.** :class:`http.server.ThreadingHTTPServer` +
  :class:`BaseHTTPRequestHandler`. No third-party deps.
* **Event fan-out.** Each session owns a list of SSE subscriber queues.
  Every emitted :class:`LoopEvent` is JSON-encoded and pushed to every
  subscriber. Disconnected subscribers are pruned on the next emit.
* **Cooperative cancel.** Each session carries a
  :class:`~chimera.core.cancellation.CancellationToken`; ``DELETE``-style
  cancel can be wired by future agents on top of the same primitive.
* **Agent factory.** The server takes an ``agent_factory(state)`` callable
  so tests can drop in a mock; the CLI wires a real provider/loop/tools.
"""
from __future__ import annotations

import json
import queue
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Protocol

from chimera.core.cancellation import CancellationToken


__all__ = [
    "OtterServer",
    "OtterSessionState",
    "AgentFactory",
    "serve_http",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
]


#: Default bind host. We default to localhost — the server has no auth by
#: default, so binding to ``0.0.0.0`` requires an explicit opt-in.
DEFAULT_HOST = "127.0.0.1"

#: Default bind port. Mirrors the upstream open-source coding agent's
#: convention so existing client tooling expecting that port can connect.
DEFAULT_PORT = 5173


# ---------------------------------------------------------------------------
# Public protocols + state
# ---------------------------------------------------------------------------


class _AgentLike(Protocol):
    """Structural type for the agent the server drives.

    The real :class:`chimera.core.agent.Agent` exposes ``async_run`` and
    ``async_run_events``. Tests inject a mock that yields canned events
    (or a fake that only implements ``async_run`` for the back-compat
    terminal-result path).
    """

    async def async_run(self, task: str, env: Any | None) -> Any:  # pragma: no cover - protocol
        ...


AgentFactory = Callable[["OtterSessionState"], _AgentLike]
"""Callable that builds (or returns) an agent for a session.

The factory receives the live :class:`OtterSessionState` so it can scope
tools or env to the session's ``working_dir``. Tests typically return a
mock with a recorded ``async_run`` method.
"""


@dataclass
class OtterSessionState:
    """Per-session state held by :class:`OtterServer`.

    Attributes:
        session_id: Opaque identifier returned to the client.
        working_dir: Working directory the agent should operate in. The
            server itself doesn't ``chdir``; the embedded environment does.
        agent: Cached agent built lazily from :class:`AgentFactory` on the
            first ``POST /session/<id>/message`` for the session.
        events: Append-only ordered log of emitted SSE events. Each entry
            is a ``{"id", "event", "data"}`` dict ready to be serialized.
        subscribers: Live SSE subscriber queues. Each queue receives a
            reference to the same event dict; the GET handler serializes
            on its own thread.
        pending_permissions: Permission-id → ``threading.Event`` map. The
            event holds an ``approved`` flag once resolved by
            ``POST /tool/approve``.
        lock: Coarse per-session lock guarding mutation of ``events`` /
            ``subscribers``.
        cancel: Cooperative cancellation token. ``POST /session/<id>/cancel``
            flips this token; the agent driver checks it between SSE
            frames and stops emitting once it's set.
    """

    session_id: str
    working_dir: str = ""
    agent: _AgentLike | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list["queue.Queue[dict[str, Any] | None]"] = field(default_factory=list)
    pending_permissions: dict[str, "_PermissionGate"] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    created_at: float = field(default_factory=time.time)
    cancel: CancellationToken = field(default_factory=CancellationToken)


def _loop_event_to_payload(ev: Any, message_id: str) -> dict[str, Any]:
    """Best-effort JSON-friendly view of a :class:`LoopEvent`.

    Real :class:`chimera.core.loop_events.LoopEvent` instances expose
    ``type``, ``data``, ``turn``, and ``timestamp``. Tests sometimes
    yield plain dicts or simple namespaces; we accept either.

    The returned dict is JSON-serializable provided ``ev.data`` is.
    """
    raw_type = getattr(ev, "type", None)
    if raw_type is None and isinstance(ev, dict):
        raw_type = ev.get("type")
    type_name: str
    enum_value = getattr(raw_type, "value", None)
    if enum_value is not None:
        type_name = str(enum_value)
    else:
        type_name = str(raw_type) if raw_type is not None else "unknown"

    data = getattr(ev, "data", None)
    if data is None and isinstance(ev, dict):
        data = ev.get("data")

    turn = getattr(ev, "turn", None)
    if turn is None and isinstance(ev, dict):
        turn = ev.get("turn")

    timestamp = getattr(ev, "timestamp", None)
    if timestamp is None and isinstance(ev, dict):
        timestamp = ev.get("timestamp")

    return {
        "message_id": message_id,
        "type": type_name,
        "data": data,
        "turn": turn,
        "timestamp": timestamp,
    }


@dataclass
class _PermissionGate:
    """Threaded primitive backing :class:`OtterServer.tool_approve`.

    A handler thread that needs user approval calls
    :meth:`OtterServer.request_permission` which blocks on
    :attr:`event` until ``POST /tool/approve`` flips :attr:`approved`.
    """

    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class OtterServer:
    """Threaded HTTP server exposing an otter agent session.

    Args:
        agent_factory: Callable invoked once per session.
        host: Bind host. Defaults to ``127.0.0.1``.
        port: Bind port. Defaults to ``5173``. Use ``0`` for an OS-chosen
            ephemeral port (handy for tests).
        auth_token: When set, every request except ``GET /healthz`` must
            carry ``Authorization: Bearer <auth_token>``. Defaults to
            ``None`` (no auth — only safe behind localhost).
        tls_cert: Path to a PEM-encoded server certificate. When set
            together with *tls_key* the server wraps its accept socket
            with :class:`ssl.SSLContext` and clients must speak TLS.
            Required when ``auth_token`` is used off-localhost so the
            bearer token is not exposed in cleartext.
        tls_key: Path to the matching PEM-encoded private key.
        commands_cwd: Project root used to discover custom slash commands
            via :func:`chimera.otter.commands.load_custom_commands`.
            Defaults to :func:`os.getcwd` at handler-call time so the
            ``GET /commands`` route always reflects the live filesystem.
            Tests inject a ``tmp_path`` so synthetic
            ``.opencode/command/*.md`` files are picked up without
            polluting ``$HOME``.

    Attributes:
        sessions: Live :class:`OtterSessionState` objects keyed by id.

    Raises:
        ValueError: When exactly one of ``tls_cert`` / ``tls_key`` is
            supplied — TLS requires both halves of the pair.
    """

    def __init__(
        self,
        agent_factory: AgentFactory | None = None,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        auth_token: str | None = None,
        tls_cert: Path | str | None = None,
        tls_key: Path | str | None = None,
        commands_cwd: Path | str | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._host = host
        self._port = port
        self._auth_token = auth_token
        # Normalize to ``Path`` so callers can pass plain strings (CLI flag
        # plumbing) or pre-built ``Path`` objects (tests) interchangeably.
        self._tls_cert: Path | None = Path(tls_cert) if tls_cert else None
        self._tls_key: Path | None = Path(tls_key) if tls_key else None
        if bool(self._tls_cert) ^ bool(self._tls_key):
            raise ValueError(
                "tls_cert and tls_key must be set together (or both unset); "
                f"got tls_cert={self._tls_cert!r}, tls_key={self._tls_key!r}"
            )
        # ``None`` means "resolve to ``os.getcwd()`` at the time of every
        # ``/commands`` call" so the route always reflects the live
        # filesystem rather than a snapshot taken at server start. Tests
        # pin a ``tmp_path`` to keep the route hermetic.
        self._commands_cwd: Path | None = (
            Path(commands_cwd) if commands_cwd is not None else None
        )
        self.sessions: dict[str, OtterSessionState] = {}
        self._sessions_lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def start(self, *, blocking: bool = True) -> ThreadingHTTPServer | None:
        """Bind the socket and begin serving.

        Args:
            blocking: When ``True`` (default) block forever in
                ``serve_forever``. When ``False`` start a daemon thread
                and return the live :class:`ThreadingHTTPServer` so the
                caller can shut it down.

        Returns:
            The :class:`ThreadingHTTPServer` when ``blocking=False``,
            ``None`` when blocking. Accessing :attr:`server_address` on
            the returned server gives the actual bound port.
        """
        handler_cls = self._build_handler_class()
        httpd = ThreadingHTTPServer((self._host, self._port), handler_cls)
        # Surface the actual bound port back to the caller in case ``port=0``.
        self._port = httpd.server_address[1]
        if self._tls_cert is not None and self._tls_key is not None:
            # Stdlib-only TLS: build a server context, load the cert chain,
            # and wrap the listening socket so every ``accept()`` returns an
            # ``SSLSocket`` to ``ThreadingHTTPServer``'s per-connection
            # dispatch. We use ``server_side=True`` so the wrapped socket
            # performs the handshake on accept.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(
                certfile=str(self._tls_cert), keyfile=str(self._tls_key)
            )
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        self._httpd = httpd
        if blocking:
            try:
                httpd.serve_forever()
            finally:
                httpd.server_close()
            return None
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self._thread = thread
        return httpd

    def shutdown(self) -> None:
        """Shut the server down and join the background thread.

        Idempotent: calling on a non-started server is a no-op.
        """
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        # Wake any SSE subscribers so they exit their generators.
        with self._sessions_lock:
            sessions = list(self.sessions.values())
        for state in sessions:
            with state.lock:
                for q in state.subscribers:
                    q.put(None)
                state.subscribers.clear()

    @property
    def port(self) -> int:
        """Actual bound port (resolved after :meth:`start`)."""
        return self._port

    # ------------------------------------------------------------------
    # Session bookkeeping (used by handlers; safe to call directly in tests)
    # ------------------------------------------------------------------

    def create_session(self, *, working_dir: str = "") -> OtterSessionState:
        """Create + register a fresh :class:`OtterSessionState`."""
        state = OtterSessionState(
            session_id=uuid.uuid4().hex,
            working_dir=working_dir,
        )
        with self._sessions_lock:
            self.sessions[state.session_id] = state
        return state

    def get_session(self, session_id: str) -> OtterSessionState | None:
        """Return the session by id, or ``None`` if unknown."""
        with self._sessions_lock:
            return self.sessions.get(session_id)

    def list_session_ids(self) -> list[str]:
        """Return a snapshot of registered session ids."""
        with self._sessions_lock:
            return list(self.sessions.keys())

    # ------------------------------------------------------------------
    # Event fan-out
    # ------------------------------------------------------------------

    def emit_event(
        self, state: OtterSessionState, event: str, data: Any
    ) -> dict[str, Any]:
        """Append + fan out a server-sent event for *state*.

        Args:
            state: Target session.
            event: SSE ``event:`` field (e.g. ``"tool_use"``).
            data: JSON-serializable payload.

        Returns:
            The stored event dict (for tests that want to assert on it).
        """
        envelope = {
            "id": str(len(state.events) + 1),
            "event": event,
            "data": data,
            "timestamp": time.time(),
        }
        with state.lock:
            state.events.append(envelope)
            subscribers = list(state.subscribers)
        for q in subscribers:
            try:
                q.put_nowait(envelope)
            except queue.Full:  # pragma: no cover - unbounded queues today
                pass
        return envelope

    def subscribe(
        self,
        state: OtterSessionState,
        *,
        last_event_id: int | None = None,
    ) -> "queue.Queue[dict[str, Any] | None]":
        """Register a fresh SSE subscriber queue and return it.

        Args:
            state: Target session.
            last_event_id: Optional resume cursor. When set, history replay
                skips every envelope whose 1-based ``id`` is less than or
                equal to this value — matching the SSE spec's
                ``Last-Event-ID`` header semantics. ``None`` (the default)
                replays the full history.

        Returns:
            A queue pre-loaded with the chosen replay slice. Live frames
            arrive on the same queue once :meth:`emit_event` fires.
        """
        q: queue.Queue[dict[str, Any] | None] = queue.Queue()
        with state.lock:
            # Replay any history so a late-attaching subscriber catches up.
            # When ``last_event_id`` is set, skip frames the client has
            # already seen so reconnects don't replay everything.
            for envelope in state.events:
                if last_event_id is not None:
                    try:
                        env_id = int(envelope["id"])
                    except (KeyError, TypeError, ValueError):
                        env_id = 0
                    if env_id <= last_event_id:
                        continue
                q.put_nowait(envelope)
            state.subscribers.append(q)
        return q

    def unsubscribe(
        self, state: OtterSessionState, q: "queue.Queue[dict[str, Any] | None]"
    ) -> None:
        """Detach a subscriber queue (idempotent)."""
        with state.lock:
            try:
                state.subscribers.remove(q)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Permission bridge
    # ------------------------------------------------------------------

    def request_permission(
        self, state: OtterSessionState, *, timeout: float | None = None
    ) -> tuple[str, bool]:
        """Block until ``POST /tool/approve`` resolves a fresh permission.

        Returns ``(permission_id, approved)``. When *timeout* fires before
        a client replies, ``approved`` is ``False``.
        """
        permission_id = uuid.uuid4().hex
        gate = _PermissionGate()
        with state.lock:
            state.pending_permissions[permission_id] = gate
        self.emit_event(
            state,
            "permission_request",
            {"permission_id": permission_id},
        )
        ok = gate.event.wait(timeout=timeout)
        with state.lock:
            state.pending_permissions.pop(permission_id, None)
        return permission_id, (gate.approved if ok else False)

    def tool_approve(
        self, session_id: str, permission_id: str, *, approved: bool
    ) -> bool:
        """Resolve a pending permission. Returns ``True`` on hit, ``False`` on miss."""
        state = self.get_session(session_id)
        if state is None:
            return False
        with state.lock:
            gate = state.pending_permissions.get(permission_id)
        if gate is None:
            return False
        gate.approved = approved
        gate.event.set()
        return True

    # ------------------------------------------------------------------
    # Custom slash commands (parity with the otter REPL dispatcher)
    # ------------------------------------------------------------------

    def _resolve_commands_cwd(self) -> "Path":
        """Return the project root for custom-command discovery.

        ``commands_cwd`` is resolved lazily so the live filesystem is
        always queried — handy when the server outlives a single project
        checkout. Tests pin the value via the constructor.
        """
        import os

        if self._commands_cwd is not None:
            return self._commands_cwd
        return Path(os.getcwd())

    def list_commands(self) -> list[dict[str, Any]]:
        """Return JSON-friendly entries for every discovered custom command.

        Each entry mirrors :class:`chimera.otter.commands.CustomCommand`'s
        public surface — ``name``, ``description``, ``args`` (a list of
        ``{name, description}`` records), and ``source`` (absolute path
        of the originating ``.md`` file). The list is sorted by name so
        clients render a stable palette.

        Import is lazy so a partial install (commands module missing)
        degrades to an empty list rather than 500-ing the whole route.
        """
        try:
            from chimera.otter.commands import (
                load_custom_commands as _load,
            )
        except ImportError:  # pragma: no cover - defensive
            return []
        cmds = _load(self._resolve_commands_cwd())
        out: list[dict[str, Any]] = []
        for name in sorted(cmds):
            cmd = cmds[name]
            out.append(
                {
                    "name": cmd.name,
                    "description": cmd.description,
                    "args": [
                        {"name": a.name, "description": a.description}
                        for a in cmd.args
                    ],
                    "source": cmd.source,
                }
            )
        return out

    def invoke_command(
        self,
        name: str,
        *,
        session_id: str,
        args: list[str] | None = None,
        kwargs: dict[str, str] | None = None,
    ) -> tuple[str, str] | None:
        """Render *name* and push the rendered prompt into *session_id*.

        Mirrors :func:`chimera.otter.slash.build_custom_command_handler`
        end-to-end: positional ``args`` map to ``$1`` / ``$2`` / …
        substitutions and ``kwargs`` map to ``$ARG_NAME`` substitutions.
        The rendered prompt is then routed through :meth:`submit_message`
        — i.e. it lands as a brand-new user turn, drives the agent, and
        fans out the same SSE events any direct ``POST /session/<id>/message``
        would.

        Args:
            name: Custom-command name (filename stem, no leading slash).
            session_id: Target session id. Must already exist.
            args: Positional arguments passed to the template renderer.
            kwargs: Named arguments passed to the template renderer.

        Returns:
            ``(message_id, rendered_text)`` on success. ``None`` if either
            the command is not registered or the session id is unknown —
            the caller maps both misses onto a 404.
        """
        try:
            from chimera.otter.commands import (
                load_custom_commands as _load,
            )
        except ImportError:  # pragma: no cover - defensive
            return None
        cmds = _load(self._resolve_commands_cwd())
        cmd = cmds.get(name)
        if cmd is None:
            return None
        state = self.get_session(session_id)
        if state is None:
            return None
        rendered = cmd.render(*(args or []), **(kwargs or {}))
        message_id = self.submit_message(state, rendered)
        return message_id, rendered

    # ------------------------------------------------------------------
    # Agent dispatch
    # ------------------------------------------------------------------

    def submit_message(
        self, state: OtterSessionState, text: str
    ) -> str:
        """Spawn a background thread that drives the agent for *text*.

        Returns the assigned message id. The agent's lifecycle events fan
        out through :meth:`emit_event` so any subscribed SSE client sees
        them in order.
        """
        message_id = uuid.uuid4().hex
        self.emit_event(
            state,
            "user_message",
            {"message_id": message_id, "text": text},
        )

        def _run() -> None:
            try:
                if state.agent is None and self._agent_factory is not None:
                    state.agent = self._agent_factory(state)
                if state.agent is None:
                    self.emit_event(
                        state,
                        "error",
                        {
                            "message_id": message_id,
                            "error": "no agent_factory configured",
                        },
                    )
                    return
                self._drive_agent(state, message_id, text)
            except Exception as exc:  # noqa: BLE001
                self.emit_event(
                    state,
                    "error",
                    {
                        "message_id": message_id,
                        "error": str(exc),
                        "exception": type(exc).__name__,
                    },
                )

        threading.Thread(target=_run, daemon=True).start()
        return message_id

    def cancel_session(self, session_id: str) -> bool:
        """Mark *session_id*'s cancellation token as cancelled.

        Args:
            session_id: Target session.

        Returns:
            ``True`` if the session existed and was signalled, ``False``
            on miss. Idempotent: calling against an already-cancelled
            session still returns ``True``.
        """
        state = self.get_session(session_id)
        if state is None:
            return False
        state.cancel.cancel()
        return True

    def _drive_agent(
        self, state: OtterSessionState, message_id: str, text: str
    ) -> None:
        """Run the agent and translate its progress into SSE events.

        Preferred path: when the agent exposes ``async_run_events`` we
        stream each :class:`~chimera.core.loop_events.LoopEvent` as its
        own SSE frame, then emit a final terminal ``result`` frame for
        back-compat. The per-step loop checks ``state.cancel`` between
        yields so a ``POST /session/<id>/cancel`` stops fan-out promptly.

        Fallback path: if the agent only exposes ``async_run`` (the
        legacy mock interface used by older tests), we ``await`` it and
        emit a single terminal ``result`` event — same behaviour as
        before this change.
        """
        import asyncio

        agent = state.agent
        assert agent is not None  # checked by the caller

        stream_factory = getattr(agent, "async_run_events", None)

        if stream_factory is not None:
            self._drive_agent_streaming(state, message_id, text, stream_factory)
            return

        # Legacy back-compat path — single terminal ``result`` event.
        result: Any = None
        try:
            result = asyncio.run(agent.async_run(text, env=None))
        except Exception as exc:  # noqa: BLE001
            self.emit_event(
                state,
                "error",
                {
                    "message_id": message_id,
                    "error": str(exc),
                    "exception": type(exc).__name__,
                },
            )
            return
        self.emit_event(
            state,
            "result",
            {
                "message_id": message_id,
                "output": getattr(result, "output", ""),
                "steps": getattr(result, "steps", 0),
                "cost": getattr(result, "cost", 0.0),
                "success": getattr(result, "success", False),
            },
        )

    def _drive_agent_streaming(
        self,
        state: OtterSessionState,
        message_id: str,
        text: str,
        stream_factory: Callable[..., AsyncIterator[Any]],
    ) -> None:
        """Stream per-step :class:`LoopEvent`s through SSE.

        Each event becomes one SSE frame via :meth:`emit_event` under the
        SSE ``event:`` field ``"loop_event"`` (data carries the LoopEvent's
        ``type``, ``data``, ``turn``, and ``timestamp``). A terminal
        ``result`` frame is always emitted last so existing clients that
        only watch for ``result`` keep working.

        Cancellation: between yields we check ``state.cancel.is_cancelled``
        and break out of the loop. The terminal ``result`` frame still
        fires — clients see ``"cancelled": true`` on it.

        Provider-level cancellation: if ``stream_factory`` accepts a
        ``cancel_event`` keyword we forward
        ``state.cancel.threading_event()`` so an in-flight provider HTTP
        request can be preempted (rather than waiting for the next yield
        boundary). This matches the wave-2 follow-up note in W6-REPORT.
        """
        import asyncio
        import inspect

        try:
            factory_sig = inspect.signature(stream_factory)
            accepts_cancel_event = "cancel_event" in factory_sig.parameters
        except (TypeError, ValueError):
            accepts_cancel_event = False

        steps = 0
        cost = 0.0
        last_text = ""
        cancelled = False
        success = True

        async def _consume() -> None:
            nonlocal steps, cost, last_text, cancelled
            if accepts_cancel_event:
                agen = stream_factory(
                    text,
                    env=None,
                    cancel_event=state.cancel.threading_event(),
                )
            else:
                agen = stream_factory(text, env=None)
            try:
                async for ev in agen:
                    if state.cancel.is_cancelled:
                        cancelled = True
                        # Try to release the underlying generator promptly.
                        aclose = getattr(agen, "aclose", None)
                        if aclose is not None:
                            try:
                                await aclose()
                            except Exception:  # noqa: BLE001 - best-effort
                                pass
                        break
                    payload = _loop_event_to_payload(ev, message_id)
                    self.emit_event(state, "loop_event", payload)
                    steps += 1
                    # Best-effort tracking of free-form fields the agent
                    # may attach to its events.
                    inner = payload.get("data")
                    if isinstance(inner, dict):
                        if isinstance(inner.get("text"), str):
                            last_text = inner["text"]
                        if isinstance(inner.get("cost_usd"), (int, float)):
                            cost = float(inner["cost_usd"])
            finally:
                aclose = getattr(agen, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:  # noqa: BLE001 - best-effort
                        pass

        try:
            asyncio.run(_consume())
        except Exception as exc:  # noqa: BLE001
            success = False
            self.emit_event(
                state,
                "error",
                {
                    "message_id": message_id,
                    "error": str(exc),
                    "exception": type(exc).__name__,
                },
            )

        self.emit_event(
            state,
            "result",
            {
                "message_id": message_id,
                "output": last_text,
                "steps": steps,
                "cost": cost,
                "success": success and not cancelled,
                "cancelled": cancelled,
            },
        )

    # ------------------------------------------------------------------
    # Handler factory (closure over self)
    # ------------------------------------------------------------------

    def _build_handler_class(self) -> type[BaseHTTPRequestHandler]:
        """Build a :class:`BaseHTTPRequestHandler` subclass closed over self.

        The closure pattern (mirroring :class:`chimera.server.webhook`)
        keeps the handler self-contained without polluting the
        ``http.server`` module-level namespace.
        """
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            # Keep stderr quiet during tests; opt back in via $OTTER_LOG=1.
            def log_message(self, format: str, *args: Any) -> None:
                import os as _os

                if _os.environ.get("OTTER_LOG") == "1":  # pragma: no cover
                    super().log_message(format, *args)

            # ------- Auth ------------------------------------------------
            def _check_auth(self) -> bool:
                if outer._auth_token is None:
                    return True
                if self.path == "/healthz":
                    return True
                got = self.headers.get("Authorization", "")
                expected = f"Bearer {outer._auth_token}"
                if got != expected:
                    self._send_json(401, {"error": "unauthorized"})
                    return False
                return True

            # ------- Helpers --------------------------------------------
            def _read_json(self) -> dict[str, Any] | None:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b""
                if not raw:
                    return {}
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "invalid_json"})
                    return None
                if not isinstance(parsed, dict):
                    self._send_json(400, {"error": "expected_json_object"})
                    return None
                return parsed

            def _send_json(self, status: int, payload: Any) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_status(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            # ------- Routing --------------------------------------------
            def do_GET(self) -> None:  # noqa: N802 - stdlib API
                if not self._check_auth():
                    return
                path = self.path.split("?", 1)[0]
                if path == "/healthz":
                    self._send_json(200, {"status": "ok"})
                    return
                if path == "/session":
                    self._send_json(200, {"sessions": outer.list_session_ids()})
                    return
                if path == "/commands":
                    return self._handle_commands_list()
                if path.startswith("/session/"):
                    parts = path.split("/")
                    # /session/<id>           parts == ["", "session", "<id>"]
                    # /session/<id>/events    parts == ["", "session", "<id>", "events"]
                    if len(parts) == 3:
                        return self._handle_session_state(parts[2])
                    if len(parts) == 4 and parts[3] == "events":
                        return self._handle_session_events(parts[2])
                self._send_json(404, {"error": "not_found", "path": path})

            def do_POST(self) -> None:  # noqa: N802 - stdlib API
                if not self._check_auth():
                    return
                path = self.path.split("?", 1)[0]
                if path == "/session":
                    return self._handle_session_create()
                if path == "/tool/approve":
                    return self._handle_tool_approve()
                if path.startswith("/session/"):
                    parts = path.split("/")
                    if len(parts) == 4 and parts[3] == "message":
                        return self._handle_session_message(parts[2])
                    if len(parts) == 4 and parts[3] == "cancel":
                        return self._handle_session_cancel(parts[2])
                if path.startswith("/commands/"):
                    parts = path.split("/")
                    # /commands/<name>/invoke
                    #   parts == ["", "commands", "<name>", "invoke"]
                    if len(parts) == 4 and parts[3] == "invoke" and parts[2]:
                        return self._handle_command_invoke(parts[2])
                self._send_json(404, {"error": "not_found", "path": path})

            # ------- POST /session --------------------------------------
            def _handle_session_create(self) -> None:
                body = self._read_json()
                if body is None:
                    return
                working_dir = str(body.get("working_dir", "") or "")
                state = outer.create_session(working_dir=working_dir)
                self._send_json(
                    201,
                    {
                        "session_id": state.session_id,
                        "working_dir": state.working_dir,
                        "created_at": state.created_at,
                    },
                )

            # ------- GET /session/<id> ----------------------------------
            def _handle_session_state(self, session_id: str) -> None:
                state = outer.get_session(session_id)
                if state is None:
                    self._send_json(404, {"error": "session_not_found"})
                    return
                with state.lock:
                    payload = {
                        "session_id": state.session_id,
                        "working_dir": state.working_dir,
                        "created_at": state.created_at,
                        "event_count": len(state.events),
                    }
                self._send_json(200, payload)

            # ------- POST /session/<id>/message -------------------------
            def _handle_session_message(self, session_id: str) -> None:
                state = outer.get_session(session_id)
                if state is None:
                    self._send_json(404, {"error": "session_not_found"})
                    return
                body = self._read_json()
                if body is None:
                    return
                text = str(body.get("text", "") or "")
                if not text:
                    self._send_json(400, {"error": "missing_text"})
                    return
                message_id = outer.submit_message(state, text)
                self._send_json(202, {"message_id": message_id})

            # ------- POST /session/<id>/cancel --------------------------
            def _handle_session_cancel(self, session_id: str) -> None:
                # Drain (and discard) any body so urllib clients don't
                # sit blocked waiting for it to be consumed.
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length > 0:
                    self.rfile.read(length)
                if not outer.cancel_session(session_id):
                    self._send_json(404, {"error": "session_not_found"})
                    return
                self._send_status(204)

            # ------- GET /session/<id>/events ---------------------------
            def _handle_session_events(self, session_id: str) -> None:
                state = outer.get_session(session_id)
                if state is None:
                    self._send_json(404, {"error": "session_not_found"})
                    return
                # Honor the SSE-spec ``Last-Event-ID`` header so reconnecting
                # clients resume from where they dropped instead of replaying
                # every persisted frame. Malformed values are ignored
                # (full replay), matching the spec's "treat as if absent."
                last_event_id: int | None = None
                raw_last = self.headers.get("Last-Event-ID")
                if raw_last is not None:
                    try:
                        last_event_id = int(raw_last.strip())
                    except ValueError:
                        last_event_id = None
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                q = outer.subscribe(state, last_event_id=last_event_id)
                try:
                    while True:
                        try:
                            envelope = q.get(timeout=15.0)
                        except queue.Empty:
                            # Heartbeat keeps proxies from killing the stream.
                            try:
                                self.wfile.write(b": keep-alive\n\n")
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                return
                            continue
                        if envelope is None:
                            return
                        try:
                            line = (
                                f"id: {envelope['id']}\n"
                                f"event: {envelope['event']}\n"
                                f"data: {json.dumps(envelope['data'])}\n\n"
                            ).encode("utf-8")
                            self.wfile.write(line)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            return
                finally:
                    outer.unsubscribe(state, q)

            # ------- POST /tool/approve --------------------------------
            def _handle_tool_approve(self) -> None:
                body = self._read_json()
                if body is None:
                    return
                session_id = str(body.get("session_id", "") or "")
                permission_id = str(body.get("permission_id", "") or "")
                approved = bool(body.get("approved", False))
                if not session_id or not permission_id:
                    self._send_json(
                        400,
                        {"error": "missing_session_id_or_permission_id"},
                    )
                    return
                ok = outer.tool_approve(
                    session_id, permission_id, approved=approved
                )
                if not ok:
                    self._send_json(404, {"error": "permission_not_found"})
                    return
                self._send_json(200, {"resolved": True, "approved": approved})

            # ------- GET /commands -------------------------------------
            def _handle_commands_list(self) -> None:
                # Discover .opencode/command/*.md (project + user scope).
                # Failures are surfaced as a 500 — a broken loader is a
                # bug, not an empty palette.
                try:
                    entries = outer.list_commands()
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        500,
                        {
                            "error": "command_load_failed",
                            "detail": str(exc),
                        },
                    )
                    return
                self._send_json(200, {"commands": entries})

            # ------- POST /commands/<name>/invoke ----------------------
            def _handle_command_invoke(self, name: str) -> None:
                body = self._read_json()
                if body is None:
                    return
                session_id = str(body.get("session_id", "") or "")
                if not session_id:
                    self._send_json(400, {"error": "missing_session_id"})
                    return

                # ``args`` accepts a list (preferred) and ``kwargs`` accepts
                # a dict. Both are coerced to strings since the renderer
                # treats placeholders as text — non-string values would
                # crash :meth:`str.replace` deep inside ``CustomCommand.render``.
                raw_args = body.get("args") or []
                if not isinstance(raw_args, list):
                    self._send_json(400, {"error": "args_must_be_list"})
                    return
                raw_kwargs = body.get("kwargs") or {}
                if not isinstance(raw_kwargs, dict):
                    self._send_json(400, {"error": "kwargs_must_be_object"})
                    return
                args: list[str] = [str(a) for a in raw_args]
                kwargs: dict[str, str] = {
                    str(k): str(v) for k, v in raw_kwargs.items()
                }

                # ``invoke_command`` returns ``None`` for both unknown
                # session and unknown command. Distinguish so the client
                # gets an actionable 404 message.
                if outer.get_session(session_id) is None:
                    self._send_json(404, {"error": "session_not_found"})
                    return
                try:
                    result = outer.invoke_command(
                        name,
                        session_id=session_id,
                        args=args,
                        kwargs=kwargs,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        500,
                        {
                            "error": "command_invoke_failed",
                            "detail": str(exc),
                        },
                    )
                    return
                if result is None:
                    self._send_json(
                        404,
                        {"error": "command_not_found", "name": name},
                    )
                    return
                message_id, rendered = result
                self._send_json(
                    202,
                    {
                        "message_id": message_id,
                        "name": name,
                        "rendered": rendered,
                    },
                )

        return _Handler


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


def serve_http(
    agent_factory: AgentFactory | None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    auth_token: str | None = None,
    tls_cert: Path | str | None = None,
    tls_key: Path | str | None = None,
    commands_cwd: Path | str | None = None,
) -> int:
    """Start :class:`OtterServer` in blocking mode and return an exit code.

    Args:
        agent_factory: Per-session agent builder. ``None`` is allowed —
            the server still serves health/session endpoints, useful for
            smoke tests where no real agent is needed.
        host: Bind host.
        port: Bind port.
        auth_token: Optional shared-secret bearer token.
        tls_cert: Optional path to a PEM-encoded server certificate.
            When supplied together with ``tls_key`` the server speaks
            HTTPS so the bearer token is not exposed in cleartext.
        tls_key: Optional path to the matching PEM-encoded private key.
        commands_cwd: Project root for ``.opencode/command/*.md``
            discovery. Defaults to :func:`os.getcwd` resolved per-call.

    Returns:
        ``0`` on graceful shutdown (Ctrl-C). The function blocks until
        the server stops.
    """
    server = OtterServer(
        agent_factory,
        host=host,
        port=port,
        auth_token=auth_token,
        tls_cert=tls_cert,
        tls_key=tls_key,
        commands_cwd=commands_cwd,
    )
    try:
        server.start(blocking=True)
    except KeyboardInterrupt:
        server.shutdown()
    return 0

"""Otter ACP (Agent Client Protocol) **server** transport.

This is the inverse of :mod:`chimera.acp.client`: where ``ACPClient`` *spawns*
an external ACP-speaking agent and drives it as a subprocess, ``OtterACPServer``
*is* the agent — it accepts ACP requests on stdin (or any byte stream) and
emits responses + ``session/update`` notifications on stdout.

The upstream open-source coding agent ships an ACP server first-class so
external IDE / TUI clients (e.g. Zed) can drive sessions without bespoke
plumbing. We mirror that surface so ``chimera otter serve --acp`` exposes
the same wire shape.

Wire protocol
-------------

* **Transport:** newline-delimited JSON-RPC 2.0 (the same flavor used in
  :mod:`chimera.acp.client` and :mod:`chimera.rpc`). One JSON object per
  line, no headers, no Content-Length framing.
* **Requests** carry an ``id``; the server responds with the same id.
* **Notifications** (server → client) have no ``id`` and use the ``method``
  ``"session/update"``; this mirrors the upstream ``connection.sessionUpdate``
  call shape.

Methods implemented
-------------------

============================  ==============================================
Method                        Purpose
============================  ==============================================
``initialize``                Capability negotiation. Returns the otter
                              agent name + version + capability bag.
``session/new``               Create a fresh agent session bound to a
                              working directory. Returns ``sessionId``.
``session/message``           Send a user prompt; the server runs the
                              wrapped Agent and streams ``session/update``
                              notifications back to the client. The reply
                              carries the final ``stopReason`` + accumulated
                              text.
``session/cancel``            Cooperatively cancel an in-flight
                              ``session/message`` for a given session.
``session/resume``            Replay ``session/update`` notifications a
                              client missed across a disconnect. Accepts
                              ``{sessionId, sinceEventId}`` and replays
                              every notification whose monotonic
                              ``eventId`` is strictly greater than the
                              cursor. Mirrors the SSE
                              ``Last-Event-ID`` resume flow used by the
                              HTTP server (``chimera/otter/server.py``).
``tool/approve``              Reply to a pending tool-permission request
                              (``approve`` / ``deny``). Mirrors the
                              upstream ``requestPermission`` flow.
============================  ==============================================

Resume / event ids
------------------

Every ``session/update`` notification carries a monotonic per-session
``eventId`` (1-based) plus the standard ``sessionId``. The server keeps
a bounded history (``ACPSessionState.event_history``) so a reconnecting
client can call ``session/resume`` with the last id it saw and pick up
exactly where it left off. ACP runs over stdio (no TCP/TLS surface to
secure), so transport security is delegated to whatever wrapper the
client uses to spawn the subprocess.

Design notes
------------

* **Stdlib only.** No SDK dep, no extra build-time deps. Just :mod:`json`
  and :mod:`asyncio`.
* **Test-friendly.** The constructor accepts an ``agent_factory`` callable
  so unit tests can drop in a mock agent. The reader/writer are also
  injectable so tests can drive the server with in-memory streams.
* **Cancellation.** Each in-flight ``session/message`` carries a
  :class:`~chimera.core.cancellation.CancellationToken`; ``session/cancel``
  sets it and the bridge loop drains.
* **Permission bridge.** ``tool/approve`` resolves an
  :class:`asyncio.Future` keyed by ``permissionId`` — outbound permission
  requests are emitted as notifications and the reply is awaited inline.

Trademark hygiene: this module never names the upstream open-source coding
agent. The ACP wire shape itself is an open spec.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "OtterACPServer",
    "ACPSessionState",
    "AgentFactory",
    "JsonValue",
    "OTTER_ACP_PROTOCOL_VERSION",
    "OTTER_ACP_AGENT_NAME",
    "serve_stdio",
]

#: Wire-level protocol version reported by ``initialize``. Bumped when the
#: server's request/response shape changes in a way clients must notice.
#: ``2`` adds per-notification ``eventId`` plus the ``session/resume`` method.
OTTER_ACP_PROTOCOL_VERSION = 2

#: Default cap on retained ``session/update`` notifications per session.
#: Older entries are evicted FIFO so a long-lived session doesn't grow
#: unbounded. Clients that drop further behind than this lose replay —
#: which matches the SSE reconnect semantics on the HTTP server.
OTTER_ACP_DEFAULT_HISTORY_SIZE = 1024

#: Name reported in ``initialize.agentInfo.name``. Trademark-clean.
OTTER_ACP_AGENT_NAME = "otter"


JsonValue = Any
"""JSON-serializable value alias used throughout this module."""


class _AgentLike(Protocol):
    """Structural type for the agent the server drives.

    The real :class:`chimera.core.agent.Agent` exposes ``async_run``; tests
    can pass any object that implements an awaitable matching this shape.
    """

    async def async_run(self, task: str, env: Any | None) -> Any:  # pragma: no cover - protocol
        ...


AgentFactory = Callable[["ACPSessionState"], _AgentLike]
"""Callable that builds (or returns) an agent for a given session.

The factory receives the live :class:`ACPSessionState` so it can scope tools
or env to the session's ``working_dir``. Tests typically return a mock with
a recorded ``async_run`` method.
"""


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class ACPSessionState:
    """Per-session state carried by the server.

    Attributes:
        session_id: Opaque ACP session identifier handed back to the client.
        working_dir: Working directory the agent should operate in. The
            server itself doesn't ``chdir`` — the embedded environment does.
        agent: Cached agent instance built lazily from the
            :class:`AgentFactory` on first ``session/message``.
        cancel_event: Set by ``session/cancel`` to signal the active turn
            should abort cooperatively.
        active_turn: ``True`` while a ``session/message`` is mid-flight.
            Guarded by :attr:`turn_lock` so a second prompt arriving while
            the first is still running is rejected (mirroring upstream).
        turn_lock: Async lock serializing per-session turn execution.
        pending_permissions: Permission-id → resolved-future map. The
            bridge fills the future when ``tool/approve`` arrives.
        last_event_id: Monotonic counter for ``session/update`` events
            emitted on this session. Starts at ``0``; the first emitted
            event is tagged ``eventId=1``.
        event_history: Bounded FIFO buffer of recent ``session/update``
            notifications, used by ``session/resume`` to replay
            notifications a reconnecting client missed.
        history_limit: Maximum number of notifications retained in
            :attr:`event_history`. Older entries are evicted FIFO.
    """

    session_id: str
    working_dir: str
    agent: _AgentLike | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    active_turn: bool = False
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_permissions: dict[str, asyncio.Future[bool]] = field(default_factory=dict)
    last_event_id: int = 0
    event_history: list[dict[str, JsonValue]] = field(default_factory=list)
    history_limit: int = OTTER_ACP_DEFAULT_HISTORY_SIZE


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class OtterACPServer:
    """JSON-RPC 2.0 ACP server speaking newline-delimited JSON over stdio.

    The server runs an ``asyncio`` read loop that pulls JSON lines off
    :attr:`reader`, dispatches each request to a method handler, and writes
    responses + notifications back through :attr:`writer`.

    Args:
        agent_factory: Callable invoked once per session. Receives the
            :class:`ACPSessionState` so the factory can scope env/tools.
        reader: Async byte source. Defaults to ``sys.stdin.buffer`` adapted
            via :meth:`asyncio.StreamReader`. Tests inject a fake.
        writer: Async byte sink. Defaults to a wrapper around
            ``sys.stdout.buffer``. Tests inject a fake.
        protocol_version: ACP wire version reported in ``initialize``.
        agent_name: Name reported in ``initialize.agentInfo.name``.
        agent_version: Version reported in ``initialize.agentInfo.version``.

    Attributes:
        sessions: Live :class:`ACPSessionState` objects keyed by session id.
        initialized: Set after the client's ``initialize`` request returns
            successfully. Other methods refuse to run until this flips.
    """

    def __init__(
        self,
        agent_factory: AgentFactory,
        *,
        reader: "_LineReader | None" = None,
        writer: "_LineWriter | None" = None,
        protocol_version: int = OTTER_ACP_PROTOCOL_VERSION,
        agent_name: str = OTTER_ACP_AGENT_NAME,
        agent_version: str = "0.0.0",
        history_limit: int = OTTER_ACP_DEFAULT_HISTORY_SIZE,
    ) -> None:
        self._agent_factory = agent_factory
        self._reader: _LineReader = reader or _StdinLineReader()
        self._writer: _LineWriter = writer or _StdoutLineWriter()
        self._protocol_version = protocol_version
        self._agent_name = agent_name
        self._agent_version = agent_version
        self._history_limit = history_limit

        self.sessions: dict[str, ACPSessionState] = {}
        self.initialized: bool = False
        self._write_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._handlers: dict[str, Callable[[JsonValue], Awaitable[JsonValue]]] = {
            "initialize": self._handle_initialize,
            "session/new": self._handle_session_new,
            "session/message": self._handle_session_message,
            "session/cancel": self._handle_session_cancel,
            "session/resume": self._handle_session_resume,
            "tool/approve": self._handle_tool_approve,
        }

    # -- public lifecycle ---------------------------------------------------

    async def serve_forever(self) -> None:
        """Read lines from :attr:`reader` until EOF or :meth:`stop`.

        Each line is parsed as JSON-RPC 2.0; valid requests are dispatched
        on a background task so a slow handler can't block the read loop.
        """
        while not self._stop.is_set():
            line = await self._reader.readline()
            if not line:
                return
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except json.JSONDecodeError as exc:
                await self._send_parse_error(exc)
                continue
            asyncio.create_task(self._dispatch(message))

    def stop(self) -> None:
        """Signal :meth:`serve_forever` to exit at the next opportunity."""
        self._stop.set()

    # -- dispatch -----------------------------------------------------------

    async def _dispatch(self, message: JsonValue) -> None:
        """Route a single inbound JSON-RPC message to its handler."""
        if not isinstance(message, dict):
            return
        method = message.get("method")
        if not isinstance(method, str):
            # Response to a server-issued request — we don't currently
            # generate any, but accept and ignore for forward-compat.
            return
        request_id = message.get("id")
        params = message.get("params", {}) or {}

        handler = self._handlers.get(method)
        if handler is None:
            if request_id is not None:
                await self._send_error(request_id, -32601, f"Method not found: {method}")
            return

        try:
            result = await handler(params)
        except _ACPError as exc:
            if request_id is not None:
                await self._send_error(request_id, exc.code, exc.message, exc.data)
            return
        except Exception as exc:  # noqa: BLE001 - surface to client
            if request_id is not None:
                await self._send_error(request_id, -32603, f"Internal error: {exc}")
            return

        if request_id is not None:
            await self._send_result(request_id, result)

    # -- handlers -----------------------------------------------------------

    async def _handle_initialize(self, params: JsonValue) -> JsonValue:
        """Capability negotiation. Mirrors upstream ``initialize`` shape."""
        client_protocol = (
            params.get("protocolVersion") if isinstance(params, dict) else None
        )
        # Accept any client version — we report ours back.
        self.initialized = True
        return {
            "protocolVersion": self._protocol_version,
            "agentInfo": {
                "name": self._agent_name,
                "version": self._agent_version,
            },
            "agentCapabilities": {
                "promptCapabilities": {"text": True},
                "sessionCapabilities": {
                    "cancel": True,
                    "resume": True,
                },
                "toolApproval": True,
                "eventIds": True,
            },
            "clientProtocolVersion": client_protocol,
        }

    async def _handle_session_new(self, params: JsonValue) -> JsonValue:
        """Allocate a new session bound to ``params.cwd`` (or ``"."``)."""
        self._require_initialized()
        cwd = "."
        if isinstance(params, dict):
            cwd = str(params.get("cwd") or params.get("working_dir") or ".")
        session_id = f"otter-{uuid.uuid4().hex[:12]}"
        state = ACPSessionState(
            session_id=session_id,
            working_dir=cwd,
            history_limit=self._history_limit,
        )
        self.sessions[session_id] = state
        return {"sessionId": session_id, "cwd": cwd}

    async def _handle_session_message(self, params: JsonValue) -> JsonValue:
        """Run a single user prompt against the agent and stream updates."""
        self._require_initialized()
        if not isinstance(params, dict):
            raise _ACPError(-32602, "session/message: params must be an object")

        session_id = params.get("sessionId") or params.get("session_id")
        message_text = params.get("message") or params.get("text") or ""
        if not isinstance(session_id, str) or session_id not in self.sessions:
            raise _ACPError(-32602, f"Unknown sessionId: {session_id!r}")
        if not isinstance(message_text, str):
            raise _ACPError(-32602, "session/message: 'message' must be a string")

        state = self.sessions[session_id]
        if state.active_turn:
            raise _ACPError(-32000, f"Session {session_id} already has an active turn")

        async with state.turn_lock:
            state.active_turn = True
            state.cancel_event.clear()
            try:
                if state.agent is None:
                    state.agent = self._agent_factory(state)

                # Notify clients the turn started — mirrors upstream
                # ``sessionUpdate: agent_message_chunk`` start signal.
                await self._notify(
                    "session/update",
                    {
                        "sessionId": session_id,
                        "update": {"sessionUpdate": "turn_start"},
                    },
                )

                run_task = asyncio.create_task(
                    self._run_agent(state, message_text),
                )
                cancel_task = asyncio.create_task(state.cancel_event.wait())
                done, pending = await asyncio.wait(
                    {run_task, cancel_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                stop_reason = "end_turn"
                output_text = ""
                error_text: str | None = None
                if cancel_task in done and run_task not in done:
                    run_task.cancel()
                    try:
                        await run_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    stop_reason = "cancelled"
                else:
                    cancel_task.cancel()
                    for pending_task in pending:
                        pending_task.cancel()
                    try:
                        result = run_task.result()
                        output_text = str(getattr(result, "output", "") or "")
                        if not getattr(result, "success", False):
                            stop_reason = "error"
                            error_text = getattr(result, "error", None)
                    except Exception as exc:  # noqa: BLE001
                        stop_reason = "error"
                        error_text = str(exc)

                if output_text:
                    await self._notify(
                        "session/update",
                        {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": output_text},
                            },
                        },
                    )

                await self._notify(
                    "session/update",
                    {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "turn_end",
                            "stopReason": stop_reason,
                        },
                    },
                )

                response: dict[str, JsonValue] = {
                    "sessionId": session_id,
                    "stopReason": stop_reason,
                    "output": output_text,
                }
                if error_text is not None:
                    response["error"] = error_text
                return response
            finally:
                state.active_turn = False
                # Drop any unresolved permission futures — they'd otherwise
                # leak across turns.
                for pid, perm_fut in list(state.pending_permissions.items()):
                    if not perm_fut.done():
                        perm_fut.cancel()
                    state.pending_permissions.pop(pid, None)

    async def _handle_session_cancel(self, params: JsonValue) -> JsonValue:
        """Set the session's cancel event so the active turn drains."""
        self._require_initialized()
        if not isinstance(params, dict):
            raise _ACPError(-32602, "session/cancel: params must be an object")
        session_id = params.get("sessionId") or params.get("session_id")
        if not isinstance(session_id, str) or session_id not in self.sessions:
            raise _ACPError(-32602, f"Unknown sessionId: {session_id!r}")
        state = self.sessions[session_id]
        state.cancel_event.set()
        return {"sessionId": session_id, "cancelled": True}

    async def _handle_session_resume(self, params: JsonValue) -> JsonValue:
        """Replay buffered ``session/update`` notifications past a cursor.

        ACP's stdio transport doesn't carry HTTP-style ``Last-Event-ID``
        headers, so we expose the same semantics as a method call. The
        client supplies the highest ``eventId`` it has already processed
        and the server re-emits every retained notification with a
        strictly larger id, in original order. Replayed notifications go
        out as plain ``session/update`` frames (same wire shape as the
        live stream) so client handlers don't need a separate code path.

        Args:
            params: ``{"sessionId": str, "sinceEventId": int}``.
                ``sinceEventId`` defaults to ``0`` (replay everything we
                still have buffered). Negative values are clamped to
                ``0``.

        Returns:
            ``{"sessionId", "replayed", "lastEventId", "truncated"}``
            where ``replayed`` is the count of notifications re-emitted,
            ``lastEventId`` is the current monotonic counter, and
            ``truncated`` is ``True`` when the cursor is older than the
            oldest retained notification (so the client may have missed
            events that fell out of the bounded buffer).
        """
        self._require_initialized()
        if not isinstance(params, dict):
            raise _ACPError(-32602, "session/resume: params must be an object")
        session_id = params.get("sessionId") or params.get("session_id")
        if not isinstance(session_id, str) or session_id not in self.sessions:
            raise _ACPError(-32602, f"Unknown sessionId: {session_id!r}")
        raw_cursor = params.get("sinceEventId")
        if raw_cursor is None:
            raw_cursor = params.get("since_event_id")
        if raw_cursor is None:
            raw_cursor = params.get("lastEventId")
        try:
            cursor = int(raw_cursor) if raw_cursor is not None else 0
        except (TypeError, ValueError):
            cursor = 0
        if cursor < 0:
            cursor = 0

        state = self.sessions[session_id]
        # Snapshot to avoid races with a live emit during replay.
        history_snapshot = list(state.event_history)
        oldest_retained = (
            int(history_snapshot[0].get("eventId", 0))
            if history_snapshot
            else state.last_event_id
        )
        truncated = bool(history_snapshot) and cursor < oldest_retained - 1

        replayed = 0
        for envelope in history_snapshot:
            try:
                eid = int(envelope.get("eventId", 0))
            except (TypeError, ValueError):
                continue
            if eid <= cursor:
                continue
            await self._write(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": envelope,
                }
            )
            replayed += 1

        return {
            "sessionId": session_id,
            "replayed": replayed,
            "lastEventId": state.last_event_id,
            "truncated": truncated,
        }

    async def _handle_tool_approve(self, params: JsonValue) -> JsonValue:
        """Resolve a pending permission future for a session.

        ``params`` shape: ``{"sessionId": ..., "permissionId": ...,
        "decision": "approve" | "deny"}``. The caller (a permission bridge
        sitting between the agent and ACP) awaits the matching future.
        """
        self._require_initialized()
        if not isinstance(params, dict):
            raise _ACPError(-32602, "tool/approve: params must be an object")
        session_id = params.get("sessionId") or params.get("session_id")
        permission_id = params.get("permissionId") or params.get("permission_id")
        decision = params.get("decision") or params.get("reply") or "deny"
        if not isinstance(session_id, str) or session_id not in self.sessions:
            raise _ACPError(-32602, f"Unknown sessionId: {session_id!r}")
        if not isinstance(permission_id, str):
            raise _ACPError(-32602, "tool/approve: 'permissionId' must be a string")
        state = self.sessions[session_id]
        fut = state.pending_permissions.pop(permission_id, None)
        if fut is None or fut.done():
            return {"sessionId": session_id, "permissionId": permission_id, "applied": False}
        fut.set_result(decision == "approve")
        return {"sessionId": session_id, "permissionId": permission_id, "applied": True}

    # -- internal: agent driver --------------------------------------------

    async def _run_agent(self, state: ACPSessionState, prompt: str) -> Any:
        """Invoke the wrapped agent.

        We intentionally don't import :mod:`chimera.env` here so tests can
        pass mock agents that ignore ``env`` entirely. Real callers wire a
        :class:`~chimera.env.local.LocalEnvironment` inside their factory.
        """
        assert state.agent is not None
        return await state.agent.async_run(prompt, env=None)

    async def request_tool_approval(
        self,
        state: ACPSessionState,
        *,
        tool_name: str,
        tool_input: JsonValue,
        timeout: float | None = None,
    ) -> bool:
        """Emit a permission notification and await the client's reply.

        This is the bridge an in-process permission checker can call; the
        permission id is generated here and resolved by ``tool/approve``.

        Args:
            state: The session whose turn is requesting approval.
            tool_name: Tool identifier for the client UI.
            tool_input: Raw tool args echoed to the client.
            timeout: Optional seconds to wait. ``None`` waits indefinitely.

        Returns:
            ``True`` if the client approved, ``False`` otherwise (including
            timeout).
        """
        permission_id = f"perm-{uuid.uuid4().hex[:10]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        state.pending_permissions[permission_id] = fut
        await self._notify(
            "session/update",
            {
                "sessionId": state.session_id,
                "update": {
                    "sessionUpdate": "permission_request",
                    "permissionId": permission_id,
                    "tool": {"name": tool_name, "input": tool_input},
                },
            },
        )
        try:
            if timeout is not None:
                return await asyncio.wait_for(fut, timeout=timeout)
            return await fut
        except (asyncio.TimeoutError, asyncio.CancelledError):
            state.pending_permissions.pop(permission_id, None)
            return False

    # -- wire helpers -------------------------------------------------------

    def _require_initialized(self) -> None:
        if not self.initialized:
            raise _ACPError(-32002, "Server not initialized; call 'initialize' first")

    async def _send_result(self, request_id: JsonValue, result: JsonValue) -> None:
        await self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _send_error(
        self,
        request_id: JsonValue,
        code: int,
        message: str,
        data: JsonValue | None = None,
    ) -> None:
        err: dict[str, JsonValue] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        await self._write({"jsonrpc": "2.0", "id": request_id, "error": err})

    async def _send_parse_error(self, exc: json.JSONDecodeError) -> None:
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
        )

    async def _notify(self, method: str, params: JsonValue) -> None:
        """Send a JSON-RPC notification, tagging session updates with an id.

        ``session/update`` notifications carry a per-session monotonic
        ``eventId`` so a reconnecting client can call ``session/resume``
        with the last id it processed and receive only the events that
        followed. The id is stamped onto ``params`` in-place; the
        envelope is also appended to the session's bounded
        :attr:`ACPSessionState.event_history` for replay. Other
        notification methods (or updates without a resolvable
        ``sessionId``) pass through unchanged.
        """
        if method == "session/update" and isinstance(params, dict):
            session_id = params.get("sessionId") or params.get("session_id")
            if isinstance(session_id, str):
                state = self.sessions.get(session_id)
                if state is not None:
                    state.last_event_id += 1
                    params["eventId"] = state.last_event_id
                    state.event_history.append(params)
                    # Bound the buffer FIFO so long-lived sessions don't
                    # leak. Slicing once when we cross the threshold
                    # amortizes to O(1) per emit.
                    if len(state.event_history) > state.history_limit:
                        overflow = len(state.event_history) - state.history_limit
                        del state.event_history[:overflow]
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, payload: JsonValue) -> None:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        async with self._write_lock:
            await self._writer.write(line.encode("utf-8"))


# ---------------------------------------------------------------------------
# Internal: line-based reader/writer protocols
# ---------------------------------------------------------------------------


class _LineReader(Protocol):
    """Async byte stream that yields one ACP frame per ``readline`` call."""

    async def readline(self) -> bytes:  # pragma: no cover - protocol
        ...


class _LineWriter(Protocol):
    """Async byte sink for newline-delimited JSON-RPC frames."""

    async def write(self, data: bytes) -> None:  # pragma: no cover - protocol
        ...


class _StdinLineReader:
    """Adapter that exposes ``sys.stdin.buffer`` as a ``_LineReader``.

    We don't use :func:`asyncio.connect_read_pipe` here because it isn't
    available on Windows ProactorEventLoop in all Python versions; pushing
    the read into a default-thread executor keeps the surface portable and
    stdlib-only.
    """

    def __init__(self) -> None:
        self._stream: Any = sys.stdin.buffer

    async def readline(self) -> bytes:
        loop = asyncio.get_running_loop()
        line: bytes = await loop.run_in_executor(None, self._stream.readline)
        return line


class _StdoutLineWriter:
    """Adapter that exposes ``sys.stdout.buffer`` as a ``_LineWriter``."""

    def __init__(self) -> None:
        self._stream: Any = sys.stdout.buffer

    async def write(self, data: bytes) -> None:
        loop = asyncio.get_running_loop()

        def _do_write() -> None:
            self._stream.write(data)
            self._stream.flush()

        await loop.run_in_executor(None, _do_write)


# ---------------------------------------------------------------------------
# Internal: error type
# ---------------------------------------------------------------------------


class _ACPError(Exception):
    """Internal carrier for JSON-RPC error replies.

    Attributes:
        code: JSON-RPC error code.
        message: Human-readable error message.
        data: Optional structured payload.
    """

    def __init__(self, code: int, message: str, data: JsonValue | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


# ---------------------------------------------------------------------------
# CLI hook — wired into ``chimera otter serve --acp``
# ---------------------------------------------------------------------------


def serve_stdio(agent_factory: AgentFactory) -> int:
    """Run :class:`OtterACPServer` on stdio until EOF; CLI entry point.

    Args:
        agent_factory: Factory used to materialize an agent per session.

    Returns:
        Process exit code (``0`` on clean shutdown, ``1`` if asyncio raised).
    """
    server = OtterACPServer(agent_factory)
    try:
        asyncio.run(server.serve_forever())
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001
        return 1

"""Tests for ``chimera.otter.server.OtterServer``.

Covers the REST + SSE surface promised by the SPEC:

* ``GET /healthz`` — liveness probe, no auth even when ``auth_token`` set.
* ``POST /session`` — creates a new session, returns an id.
* ``GET /session`` — lists registered sessions.
* ``GET /session/<id>`` — returns a state snapshot.
* ``POST /session/<id>/message`` — submits a user prompt; events fan out.
* ``GET /session/<id>/events`` — SSE stream of agent events.
* ``POST /tool/approve`` — resolves a pending permission gate.
* Auth — ``Authorization: Bearer <token>`` is required when configured.

Tests stay stdlib-only: no httpx, no requests. The server itself is
stdlib (``http.server.ThreadingHTTPServer``); we drive it with
:mod:`urllib.request` and parse SSE from a raw socket read.
"""
from __future__ import annotations

import dataclasses
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

import pytest

from chimera.otter import server as otter_server
from chimera.otter.server import OtterServer, OtterSessionState


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeAgentResult:
    """Minimal stand-in for :class:`chimera.types.AgentResult`."""

    output: str = "ok"
    steps: int = 1
    cost: float = 0.0
    success: bool = True


class _FakeAgent:
    """Records prompts and returns a canned :class:`_FakeAgentResult`."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def async_run(self, task: str, env: Any | None) -> _FakeAgentResult:
        self.prompts.append(task)
        return _FakeAgentResult(output=f"echo: {task}")


@dataclasses.dataclass
class _FakeLoopEvent:
    """Minimal stand-in for :class:`chimera.core.loop_events.LoopEvent`."""

    type: str
    data: Any
    turn: int = 0
    timestamp: float = 0.0


class _StreamingFakeAgent:
    """Yields a canned sequence of :class:`_FakeLoopEvent`s.

    Mirrors :meth:`chimera.core.agent.Agent.async_run_events` shape: an
    async iterator of ``LoopEvent``-like records. Used to exercise the
    per-step SSE emission path.
    """

    def __init__(
        self,
        events: list[_FakeLoopEvent] | None = None,
        *,
        per_event_delay: float = 0.0,
    ) -> None:
        self.events = events or [
            _FakeLoopEvent(type="assistant_chunk", data={"text": "hello"}),
            _FakeLoopEvent(type="tool_use", data={"name": "bash"}),
            _FakeLoopEvent(type="assistant", data={"text": "world"}),
        ]
        self.per_event_delay = per_event_delay
        self.prompts: list[str] = []

    async def async_run_events(self, task: str, env: Any | None = None) -> Any:
        import asyncio as _asyncio

        self.prompts.append(task)
        for ev in self.events:
            if self.per_event_delay > 0:
                await _asyncio.sleep(self.per_event_delay)
            yield ev

    async def async_run(self, task: str, env: Any | None) -> _FakeAgentResult:
        # Present so a hypothetical caller that ignores ``async_run_events``
        # still has a fallback. The server prefers the streaming method.
        self.prompts.append(task)
        return _FakeAgentResult(output="streamed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_agent() -> _FakeAgent:
    """A fresh :class:`_FakeAgent` per test."""
    return _FakeAgent()


@pytest.fixture()
def server(fake_agent: _FakeAgent) -> Iterator[OtterServer]:
    """Spin up :class:`OtterServer` on an OS-chosen port for the test."""
    srv = OtterServer(
        agent_factory=lambda _state: fake_agent,
        host="127.0.0.1",
        port=0,
    )
    srv.start(blocking=False)
    try:
        yield srv
    finally:
        srv.shutdown()


@pytest.fixture()
def auth_server() -> Iterator[OtterServer]:
    """Server bound with an auth token for auth tests."""
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=0,
        auth_token="secret-token",
    )
    srv.start(blocking=False)
    try:
        yield srv
    finally:
        srv.shutdown()


def _base_url(srv: OtterServer) -> str:
    return f"http://127.0.0.1:{srv.port}"


def _http_json(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """Minimal urllib helper that always returns ``(status, json_body)``."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw.decode("utf-8", "replace")}
    raw = resp.read()
    return resp.status, json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# Healthz + auth
# ---------------------------------------------------------------------------


def test_healthz_returns_ok(server: OtterServer) -> None:
    status, body = _http_json("GET", f"{_base_url(server)}/healthz")
    assert status == 200
    assert body == {"status": "ok"}


def test_healthz_does_not_require_auth(auth_server: OtterServer) -> None:
    """``/healthz`` must answer even when an auth token is configured."""
    status, body = _http_json("GET", f"{_base_url(auth_server)}/healthz")
    assert status == 200
    assert body["status"] == "ok"


def test_other_routes_require_auth(auth_server: OtterServer) -> None:
    status, body = _http_json(
        "POST", f"{_base_url(auth_server)}/session", body={}
    )
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_auth_token_accepted(auth_server: OtterServer) -> None:
    status, body = _http_json(
        "POST",
        f"{_base_url(auth_server)}/session",
        body={},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert status == 201
    assert "session_id" in body


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def test_post_session_creates_id(server: OtterServer) -> None:
    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/session",
        body={"working_dir": "/tmp/foo"},
    )
    assert status == 201
    assert body["working_dir"] == "/tmp/foo"
    assert isinstance(body["session_id"], str) and len(body["session_id"]) > 0


def test_get_session_list(server: OtterServer) -> None:
    _, c1 = _http_json("POST", f"{_base_url(server)}/session", body={})
    _, c2 = _http_json("POST", f"{_base_url(server)}/session", body={})
    status, body = _http_json("GET", f"{_base_url(server)}/session")
    assert status == 200
    assert set(body["sessions"]) == {c1["session_id"], c2["session_id"]}


def test_get_session_state(server: OtterServer) -> None:
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]
    status, body = _http_json("GET", f"{_base_url(server)}/session/{sid}")
    assert status == 200
    assert body["session_id"] == sid
    assert body["event_count"] == 0


def test_get_unknown_session_returns_404(server: OtterServer) -> None:
    status, body = _http_json("GET", f"{_base_url(server)}/session/missing")
    assert status == 404
    assert body == {"error": "session_not_found"}


# ---------------------------------------------------------------------------
# Messaging + agent dispatch
# ---------------------------------------------------------------------------


def test_post_message_runs_agent(
    server: OtterServer, fake_agent: _FakeAgent
) -> None:
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]
    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/session/{sid}/message",
        body={"text": "hello otter"},
    )
    assert status == 202
    assert "message_id" in body

    # Wait briefly for the background agent thread to complete.
    deadline = time.time() + 3.0
    while time.time() < deadline:
        state = server.get_session(sid)
        assert state is not None
        if any(ev["event"] == "result" for ev in state.events):
            break
        time.sleep(0.02)
    state = server.get_session(sid)
    assert state is not None
    assert "hello otter" in fake_agent.prompts
    kinds = [ev["event"] for ev in state.events]
    assert "user_message" in kinds
    assert "result" in kinds
    result_event = next(ev for ev in state.events if ev["event"] == "result")
    assert result_event["data"]["output"] == "echo: hello otter"
    assert result_event["data"]["success"] is True


def test_post_message_missing_text_is_400(server: OtterServer) -> None:
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]
    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/session/{sid}/message",
        body={},
    )
    assert status == 400
    assert body == {"error": "missing_text"}


def test_post_message_unknown_session_is_404(server: OtterServer) -> None:
    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/session/missing/message",
        body={"text": "hi"},
    )
    assert status == 404


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


def _read_sse_chunk(srv: OtterServer, sid: str, *, max_bytes: int = 4096) -> bytes:
    """Open ``/session/<id>/events`` over a raw socket and read one chunk.

    Using a raw socket (rather than ``urllib.request``) lets us pull
    bytes off the stream as they arrive without waiting for the
    server to close the connection.
    """
    s = socket.create_connection(("127.0.0.1", srv.port), timeout=5.0)
    s.sendall(
        (
            f"GET /session/{sid}/events HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{srv.port}\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
    )
    buf = b""
    deadline = time.time() + 5.0
    while time.time() < deadline and len(buf) < max_bytes:
        try:
            chunk = s.recv(max_bytes)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        # Stop once we've seen at least two SSE event blocks or the
        # initial replayed event.
        if buf.count(b"\n\n") >= 2:
            break
    s.close()
    return buf


def test_sse_stream_emits_events(
    server: OtterServer, fake_agent: _FakeAgent
) -> None:
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]

    received: list[bytes] = []

    def _consume() -> None:
        received.append(_read_sse_chunk(server, sid))

    consumer = threading.Thread(target=_consume, daemon=True)
    consumer.start()
    # Give the consumer a moment to subscribe, then send a message.
    time.sleep(0.1)
    _http_json(
        "POST",
        f"{_base_url(server)}/session/{sid}/message",
        body={"text": "ping"},
    )
    consumer.join(timeout=5.0)
    assert received, "consumer thread didn't return"
    raw = received[0].decode("utf-8", "replace")
    # SSE lines look like: ``id: 1\nevent: user_message\ndata: {...}\n\n``
    assert "event: user_message" in raw
    assert "data: " in raw
    # Find a data: line and confirm it parses as JSON.
    data_lines = [
        line[len("data: ") :]
        for line in raw.splitlines()
        if line.startswith("data: ")
    ]
    assert data_lines
    json.loads(data_lines[0])


def test_sse_replays_history_to_late_subscribers(
    server: OtterServer,
) -> None:
    """A subscriber attaching after events were emitted still sees them."""
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]
    state = server.get_session(sid)
    assert state is not None
    server.emit_event(state, "preexisting", {"hello": "world"})
    raw = _read_sse_chunk(server, sid).decode("utf-8", "replace")
    assert "event: preexisting" in raw
    assert '"hello": "world"' in raw


# ---------------------------------------------------------------------------
# SSE Last-Event-ID resume (W7)
# ---------------------------------------------------------------------------


def _read_sse_chunk_with_header(
    srv: OtterServer,
    sid: str,
    *,
    last_event_id: str | None = None,
    max_bytes: int = 4096,
    extra_blocks: int = 1,
) -> bytes:
    """Open ``/session/<id>/events`` with an optional ``Last-Event-ID`` header.

    Mirrors :func:`_read_sse_chunk` but lets the test pass arbitrary
    request headers and gate how many SSE blocks to wait for.
    """
    headers = (
        f"GET /session/{sid}/events HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{srv.port}\r\n"
        "Accept: text/event-stream\r\n"
        "Connection: close\r\n"
    )
    if last_event_id is not None:
        headers += f"Last-Event-ID: {last_event_id}\r\n"
    headers += "\r\n"
    s = socket.create_connection(("127.0.0.1", srv.port), timeout=5.0)
    s.sendall(headers.encode("ascii"))
    buf = b""
    deadline = time.time() + 5.0
    while time.time() < deadline and len(buf) < max_bytes:
        try:
            chunk = s.recv(max_bytes)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        if buf.count(b"\n\n") >= extra_blocks:
            break
    s.close()
    return buf


def _parse_sse_ids(raw: str) -> list[int]:
    """Pull integer ``id:`` values out of an SSE chunk, in order."""
    ids: list[int] = []
    for line in raw.splitlines():
        if line.startswith("id: "):
            try:
                ids.append(int(line[len("id: ") :].strip()))
            except ValueError:
                pass
    return ids


def test_sse_subscribe_with_last_event_id_skips_replay(
    server: OtterServer,
) -> None:
    """``OtterServer.subscribe`` with a cursor skips matching replay frames."""
    state = server.create_session(working_dir="/x")
    server.emit_event(state, "first", {"n": 1})
    server.emit_event(state, "second", {"n": 2})
    server.emit_event(state, "third", {"n": 3})

    q = server.subscribe(state, last_event_id=2)
    drained: list[dict[str, Any]] = []
    while True:
        try:
            env = q.get_nowait()
        except Exception:  # queue.Empty
            break
        if env is None:
            break
        drained.append(env)

    # Only the third event (id=3) should have been replayed.
    assert [env["id"] for env in drained] == ["3"]
    assert drained[0]["event"] == "third"


def test_sse_subscribe_last_event_id_beyond_history_replays_nothing(
    server: OtterServer,
) -> None:
    """A cursor past the current count drops every replay frame."""
    state = server.create_session(working_dir="/x")
    server.emit_event(state, "first", {"n": 1})
    server.emit_event(state, "second", {"n": 2})

    q = server.subscribe(state, last_event_id=99)
    # Nothing should be queued.
    import queue as _queue

    with pytest.raises(_queue.Empty):
        q.get_nowait()

    # Live frames after subscribe still come through.
    server.emit_event(state, "live", {"n": 3})
    env = q.get(timeout=2.0)
    assert env is not None
    assert env["event"] == "live"
    assert env["id"] == "3"


def test_sse_http_resume_with_last_event_id_header(
    server: OtterServer,
) -> None:
    """A reconnecting HTTP client with ``Last-Event-ID`` skips earlier frames."""
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]
    state = server.get_session(sid)
    assert state is not None

    # Pre-seed three events so we have ids 1, 2, 3 to choose from.
    server.emit_event(state, "alpha", {"n": 1})
    server.emit_event(state, "beta", {"n": 2})
    server.emit_event(state, "gamma", {"n": 3})

    # First connection: drop after seeing 2 frames (no Last-Event-ID).
    raw_first = _read_sse_chunk_with_header(
        server, sid, extra_blocks=2, max_bytes=4096
    ).decode("utf-8", "replace")
    first_ids = _parse_sse_ids(raw_first)
    assert first_ids[:2] == [1, 2], f"expected ids 1,2 first; got {first_ids}"

    # Reconnect with Last-Event-ID: 2. Only id=3 should arrive.
    raw_second = _read_sse_chunk_with_header(
        server, sid, last_event_id="2", extra_blocks=1, max_bytes=4096
    ).decode("utf-8", "replace")
    second_ids = _parse_sse_ids(raw_second)
    assert 1 not in second_ids
    assert 2 not in second_ids
    assert 3 in second_ids
    assert "event: gamma" in raw_second
    assert "event: alpha" not in raw_second
    assert "event: beta" not in raw_second


def test_sse_http_resume_malformed_last_event_id_replays_all(
    server: OtterServer,
) -> None:
    """A non-integer ``Last-Event-ID`` is ignored (full replay)."""
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]
    state = server.get_session(sid)
    assert state is not None
    server.emit_event(state, "alpha", {"n": 1})
    server.emit_event(state, "beta", {"n": 2})

    raw = _read_sse_chunk_with_header(
        server, sid, last_event_id="not-an-int", extra_blocks=2, max_bytes=4096
    ).decode("utf-8", "replace")
    ids = _parse_sse_ids(raw)
    assert 1 in ids
    assert 2 in ids


def test_sse_http_resume_zero_replays_everything(
    server: OtterServer,
) -> None:
    """``Last-Event-ID: 0`` is a valid cursor below the first id (1)."""
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]
    state = server.get_session(sid)
    assert state is not None
    server.emit_event(state, "alpha", {"n": 1})
    server.emit_event(state, "beta", {"n": 2})

    raw = _read_sse_chunk_with_header(
        server, sid, last_event_id="0", extra_blocks=2, max_bytes=4096
    ).decode("utf-8", "replace")
    ids = _parse_sse_ids(raw)
    assert ids[:2] == [1, 2]


# ---------------------------------------------------------------------------
# Permission bridge
# ---------------------------------------------------------------------------


def test_tool_approve_resolves_gate(server: OtterServer) -> None:
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]
    state = server.get_session(sid)
    assert state is not None

    holder: dict[str, Any] = {}

    def _ask() -> None:
        permission_id, approved = server.request_permission(state, timeout=3.0)
        holder["permission_id"] = permission_id
        holder["approved"] = approved

    asker = threading.Thread(target=_ask, daemon=True)
    asker.start()

    # Wait for the permission_request event to land before approving.
    deadline = time.time() + 2.0
    pid: str | None = None
    while time.time() < deadline:
        for ev in state.events:
            if ev["event"] == "permission_request":
                pid = ev["data"]["permission_id"]
                break
        if pid:
            break
        time.sleep(0.02)
    assert pid is not None, "permission_request event was never emitted"

    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/tool/approve",
        body={
            "session_id": sid,
            "permission_id": pid,
            "approved": True,
        },
    )
    assert status == 200
    assert body == {"resolved": True, "approved": True}

    asker.join(timeout=2.0)
    assert holder.get("approved") is True
    assert holder.get("permission_id") == pid


def test_tool_approve_unknown_returns_404(server: OtterServer) -> None:
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]
    status, body = _http_json(
        "POST",
        f"{_base_url(server)}/tool/approve",
        body={
            "session_id": sid,
            "permission_id": "does-not-exist",
            "approved": True,
        },
    )
    assert status == 404
    assert body == {"error": "permission_not_found"}


def test_tool_approve_missing_fields_is_400(server: OtterServer) -> None:
    status, body = _http_json(
        "POST", f"{_base_url(server)}/tool/approve", body={}
    )
    assert status == 400
    assert "missing" in body["error"]


# ---------------------------------------------------------------------------
# 404s + bad JSON
# ---------------------------------------------------------------------------


def test_unknown_route_returns_404(server: OtterServer) -> None:
    status, body = _http_json("GET", f"{_base_url(server)}/nope")
    assert status == 404
    assert body["error"] == "not_found"


def test_invalid_json_body_is_400(server: OtterServer) -> None:
    req = urllib.request.Request(
        f"{_base_url(server)}/session",
        data=b"not json",
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    try:
        urllib.request.urlopen(req, timeout=3.0)
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
        body = json.loads(exc.read())
        assert body == {"error": "invalid_json"}
    else:  # pragma: no cover - defensive
        pytest.fail("expected 400 from invalid JSON body")


# ---------------------------------------------------------------------------
# Direct API smoke
# ---------------------------------------------------------------------------


def test_emit_event_and_subscribe_in_process(server: OtterServer) -> None:
    """Sanity check :meth:`OtterServer.emit_event` without going through HTTP."""
    state = server.create_session(working_dir="/x")
    q = server.subscribe(state)
    server.emit_event(state, "ping", {"n": 1})
    envelope = q.get(timeout=2.0)
    assert envelope is not None
    assert envelope["event"] == "ping"
    assert envelope["data"] == {"n": 1}


def test_module_exports() -> None:
    assert "OtterServer" in otter_server.__all__
    assert "OtterSessionState" in otter_server.__all__
    assert "serve_http" in otter_server.__all__
    assert OtterSessionState.__name__ == "OtterSessionState"


# ---------------------------------------------------------------------------
# Cancellation + per-step SSE streaming (W6)
# ---------------------------------------------------------------------------


@pytest.fixture()
def streaming_agent() -> _StreamingFakeAgent:
    """Default :class:`_StreamingFakeAgent` for streaming-path tests."""
    return _StreamingFakeAgent()


@pytest.fixture()
def streaming_server(
    streaming_agent: _StreamingFakeAgent,
) -> Iterator[OtterServer]:
    """:class:`OtterServer` wired with a streaming fake agent."""
    srv = OtterServer(
        agent_factory=lambda _state: streaming_agent,
        host="127.0.0.1",
        port=0,
    )
    srv.start(blocking=False)
    try:
        yield srv
    finally:
        srv.shutdown()


def test_session_has_cancel_token() -> None:
    """Every fresh session ships a default :class:`CancellationToken`."""
    from chimera.core.cancellation import CancellationToken

    srv = OtterServer(agent_factory=None, host="127.0.0.1", port=0)
    state = srv.create_session(working_dir="/x")
    assert isinstance(state.cancel, CancellationToken)
    assert state.cancel.is_cancelled is False


def test_post_session_cancel_returns_204_and_flips_token(
    server: OtterServer,
) -> None:
    """``POST /session/<id>/cancel`` returns 204 and sets the cancel flag."""
    _, created = _http_json("POST", f"{_base_url(server)}/session", body={})
    sid = created["session_id"]
    state = server.get_session(sid)
    assert state is not None
    assert state.cancel.is_cancelled is False

    req = urllib.request.Request(
        f"{_base_url(server)}/session/{sid}/cancel",
        data=b"",
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=3.0)
    assert resp.status == 204
    body = resp.read()
    # 204 must not carry a body.
    assert body == b""

    assert state.cancel.is_cancelled is True


def test_post_session_cancel_unknown_returns_404(server: OtterServer) -> None:
    status, body = _http_json(
        "POST", f"{_base_url(server)}/session/missing/cancel", body=None
    )
    assert status == 404
    assert body == {"error": "session_not_found"}


def test_streaming_run_emits_per_step_events(
    streaming_server: OtterServer, streaming_agent: _StreamingFakeAgent
) -> None:
    """Streaming agent yields >1 SSE frame; terminal ``result`` still last."""
    _, created = _http_json(
        "POST", f"{_base_url(streaming_server)}/session", body={}
    )
    sid = created["session_id"]
    _http_json(
        "POST",
        f"{_base_url(streaming_server)}/session/{sid}/message",
        body={"text": "ping"},
    )

    deadline = time.time() + 5.0
    while time.time() < deadline:
        state = streaming_server.get_session(sid)
        assert state is not None
        if any(ev["event"] == "result" for ev in state.events):
            break
        time.sleep(0.02)

    state = streaming_server.get_session(sid)
    assert state is not None
    kinds = [ev["event"] for ev in state.events]
    # 1x user_message + 3x loop_event + 1x result
    assert kinds.count("loop_event") == 3
    assert kinds[-1] == "result"
    assert "ping" in streaming_agent.prompts

    # Every emitted loop_event payload must carry a recognizable LoopEvent
    # shape ({type, data, turn, timestamp, message_id}) and JSON-encode.
    for ev in state.events:
        if ev["event"] != "loop_event":
            continue
        payload = ev["data"]
        assert {"type", "data", "turn", "timestamp", "message_id"} <= payload.keys()
        json.dumps(payload)

    # Terminal result frame summarizes the run.
    result = next(ev for ev in state.events if ev["event"] == "result")
    assert result["data"]["steps"] == 3
    assert result["data"]["cancelled"] is False
    assert result["data"]["success"] is True


def test_streaming_run_sse_pushes_multiple_frames(
    streaming_server: OtterServer,
) -> None:
    """SSE socket reader sees >1 ``data:`` line for a streaming run."""
    _, created = _http_json(
        "POST", f"{_base_url(streaming_server)}/session", body={}
    )
    sid = created["session_id"]

    received: list[bytes] = []

    def _consume() -> None:
        # Pull a generous number of bytes; we expect at least 5 SSE frames.
        received.append(_read_sse_chunk(streaming_server, sid, max_bytes=8192))

    consumer = threading.Thread(target=_consume, daemon=True)
    consumer.start()
    time.sleep(0.1)
    _http_json(
        "POST",
        f"{_base_url(streaming_server)}/session/{sid}/message",
        body={"text": "ping"},
    )
    consumer.join(timeout=5.0)
    assert received, "consumer thread didn't return"
    raw = received[0].decode("utf-8", "replace")
    # The chunk reader stops after >=2 SSE blocks; we just need to
    # confirm streaming actually emits more than the single legacy frame.
    assert raw.count("\ndata: ") >= 2 or raw.count("data: ") >= 2


def test_cancel_mid_run_stops_subsequent_sse_frames() -> None:
    """Cancelling mid-run halts loop_event emission; final result reports it."""
    # Slow streaming agent: 6 events at 80ms apart so we have ~480ms total
    # to fire a cancel after the first event lands.
    slow_events = [
        _FakeLoopEvent(type="assistant_chunk", data={"text": f"chunk-{i}"})
        for i in range(6)
    ]
    agent = _StreamingFakeAgent(events=slow_events, per_event_delay=0.08)
    srv = OtterServer(
        agent_factory=lambda _state: agent,
        host="127.0.0.1",
        port=0,
    )
    srv.start(blocking=False)
    try:
        _, created = _http_json("POST", f"{_base_url(srv)}/session", body={})
        sid = created["session_id"]

        _http_json(
            "POST",
            f"{_base_url(srv)}/session/{sid}/message",
            body={"text": "go"},
        )
        # Wait for the run to actually start emitting before we cancel.
        deadline = time.time() + 2.0
        state = srv.get_session(sid)
        assert state is not None
        while time.time() < deadline:
            if sum(1 for ev in state.events if ev["event"] == "loop_event") >= 1:
                break
            time.sleep(0.01)
        # Fire cancel.
        req = urllib.request.Request(
            f"{_base_url(srv)}/session/{sid}/cancel",
            data=b"",
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=3.0)
        assert resp.status == 204

        # Wait for the run to wind down (terminal result lands).
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if any(ev["event"] == "result" for ev in state.events):
                break
            time.sleep(0.02)

        assert state.cancel.is_cancelled is True
        loop_event_count = sum(
            1 for ev in state.events if ev["event"] == "loop_event"
        )
        # We must have stopped before draining all 6 events.
        assert 1 <= loop_event_count < 6, (
            f"expected mid-run cancel; saw {loop_event_count}/6 loop_events"
        )

        # And no further loop_events should land after the result frame.
        kinds = [ev["event"] for ev in state.events]
        result_idx = kinds.index("result")
        assert "loop_event" not in kinds[result_idx + 1 :]

        # Terminal result reports the cancel.
        result = next(ev for ev in state.events if ev["event"] == "result")
        assert result["data"]["cancelled"] is True
        assert result["data"]["success"] is False
    finally:
        srv.shutdown()

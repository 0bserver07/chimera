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

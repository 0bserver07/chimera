"""Tests for multi-session :class:`OtterServer` + :class:`OtterSessionManager`.

Wave-9 C2 contract: ``chimera otter serve`` (and ferret HTTP) must
support many concurrent sessions — created, messaged, observed, and
torn down independently. This test file pins:

* :class:`chimera.otter.server.OtterSessionManager` lifecycle
  (``create``/``get``/``delete``/``list_active``/``evict_idle``).
* New HTTP routes ``GET /sessions`` and ``DELETE /session/<id>``.
* Concurrent agent runs across two sessions don't cross-contaminate
  each other's SSE replay buffers.
* TTL eviction reaps idle sessions and wakes their SSE subscribers.
* Existing 70 server tests still pass (covered by ``test_server*.py``).
"""
from __future__ import annotations

import dataclasses
import json
import socket
import threading
import time
import urllib.request
from typing import Any, Iterator

import pytest

from chimera.otter import server as otter_server
from chimera.otter.server import (
    DEFAULT_SESSION_TTL,
    OtterServer,
    OtterSessionManager,
    OtterSessionState,
)


# ---------------------------------------------------------------------------
# Test fakes (mirror tests/otter/test_server.py)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeAgentResult:
    output: str = "ok"
    steps: int = 1
    cost: float = 0.0
    success: bool = True


class _SessionAwareAgent:
    """Echoes the session id into its output so cross-contamination is visible.

    The agent factory passes the live :class:`OtterSessionState` so we
    can stamp ``state.session_id`` onto every recorded prompt and the
    agent's terminal output. If session A's events ever land on session
    B's stream the assertion ``output endswith state.session_id`` will
    fail.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.prompts: list[str] = []

    async def async_run(self, task: str, env: Any | None) -> _FakeAgentResult:
        self.prompts.append(task)
        return _FakeAgentResult(output=f"{task}::{self.session_id}")


def _make_factory() -> Any:
    """Return a factory that builds a fresh :class:`_SessionAwareAgent`."""

    def _factory(state: OtterSessionState) -> _SessionAwareAgent:
        return _SessionAwareAgent(session_id=state.session_id)

    return _factory


# ---------------------------------------------------------------------------
# HTTP fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server() -> Iterator[OtterServer]:
    """A fresh :class:`OtterServer` with a session-aware factory."""
    srv = OtterServer(
        agent_factory=_make_factory(),
        host="127.0.0.1",
        port=0,
    )
    srv.start(blocking=False)
    try:
        yield srv
    finally:
        srv.shutdown()


def _base_url(srv: OtterServer) -> str:
    return f"http://127.0.0.1:{srv.port}"


def _http(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """Stdlib helper that returns ``(status, json_body)``."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:  # noqa: F821 - imported lazily
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw.decode("utf-8", "replace")}
    raw = resp.read()
    return resp.status, json.loads(raw) if raw else {}


# ``urllib.error`` import gymnastics: pulled in here so the
# ``HTTPError`` reference inside ``_http`` resolves without a global
# import at the top of the module (some callers monkeypatch
# ``urllib.request`` and we don't want to surface the helper as part of
# the public test surface).
import urllib.error  # noqa: E402


# ---------------------------------------------------------------------------
# OtterSessionManager — pure unit tests (no HTTP)
# ---------------------------------------------------------------------------


def test_manager_create_returns_distinct_states() -> None:
    """Two ``create`` calls produce two distinct :class:`OtterSessionState`."""
    mgr = OtterSessionManager(ttl=None)
    a = mgr.create(working_dir="/a")
    b = mgr.create(working_dir="/b")
    assert a.session_id != b.session_id
    assert a.working_dir == "/a"
    assert b.working_dir == "/b"
    assert set(mgr.list_ids()) == {a.session_id, b.session_id}


def test_manager_get_returns_session_and_bumps_last_touched() -> None:
    """A live ``get`` updates ``last_touched`` so the session stays alive."""
    clock = [1000.0]
    mgr = OtterSessionManager(ttl=None, clock=lambda: clock[0])
    state = mgr.create()
    original = state.last_touched
    clock[0] = 1500.0
    fetched = mgr.get(state.session_id)
    assert fetched is state
    assert state.last_touched > original
    assert state.last_touched == 1500.0


def test_manager_get_with_touch_false_does_not_bump() -> None:
    """``get(touch=False)`` reads the session without resetting the clock."""
    clock = [1000.0]
    mgr = OtterSessionManager(ttl=None, clock=lambda: clock[0])
    state = mgr.create()
    original = state.last_touched
    clock[0] = 1500.0
    fetched = mgr.get(state.session_id, touch=False)
    assert fetched is state
    assert state.last_touched == original


def test_manager_get_unknown_id_returns_none() -> None:
    mgr = OtterSessionManager(ttl=None)
    assert mgr.get("does-not-exist") is None


def test_manager_delete_removes_and_returns_state() -> None:
    """``delete`` returns the state on hit and ``None`` on miss."""
    mgr = OtterSessionManager(ttl=None)
    state = mgr.create()
    deleted = mgr.delete(state.session_id)
    assert deleted is state
    # Idempotent — second delete is a miss.
    assert mgr.delete(state.session_id) is None
    assert state.session_id not in mgr.list_ids()


def test_manager_delete_wakes_sse_subscribers_and_releases_gates() -> None:
    """Doomed sessions fire ``None`` to subscribers + set permission gates."""
    import queue

    mgr = OtterSessionManager(ttl=None)
    state = mgr.create()
    q: queue.Queue[dict[str, Any] | None] = queue.Queue()
    state.subscribers.append(q)
    from chimera.otter.server import _PermissionGate

    gate = _PermissionGate()
    state.pending_permissions["pid-1"] = gate

    mgr.delete(state.session_id)

    # The subscriber must see ``None`` so its consumer generator exits.
    assert q.get(timeout=1.0) is None
    # The gate must be set so any blocked thread unwinds.
    assert gate.event.is_set() is True
    # Cancellation token must be flipped so an in-flight agent run halts.
    assert state.cancel.is_cancelled is True


def test_manager_evict_idle_drops_old_sessions() -> None:
    """Sessions older than ``ttl`` are reaped on ``evict_idle``."""
    clock = [1000.0]
    mgr = OtterSessionManager(ttl=60.0, clock=lambda: clock[0])
    fresh = mgr.create()
    stale = mgr.create()
    # Move the clock forward and bump only the "fresh" session.
    clock[0] = 1100.0
    mgr.touch(fresh.session_id)
    # Now ``stale.last_touched`` is 1000 and the cutoff is
    # 1100 - 60 = 1040 → stale must be evicted, fresh kept.
    evicted = mgr.evict_idle()
    assert evicted == [stale.session_id]
    assert fresh.session_id in mgr.list_ids()
    assert stale.session_id not in mgr.list_ids()
    # Eviction tears down the session.
    assert stale.cancel.is_cancelled is True


def test_manager_evict_idle_disabled_when_ttl_falsy() -> None:
    """``ttl=None`` (or 0) means *never* evict."""
    clock = [1000.0]
    mgr = OtterSessionManager(ttl=None, clock=lambda: clock[0])
    state = mgr.create()
    clock[0] = 9_999_999.0
    assert mgr.evict_idle() == []
    assert state.session_id in mgr.list_ids()


def test_manager_list_active_returns_sorted_metadata() -> None:
    """``list_active`` yields newest-touched first with full metadata."""
    clock = [1000.0]
    mgr = OtterSessionManager(ttl=None, clock=lambda: clock[0])
    older = mgr.create(working_dir="/older")
    clock[0] = 1100.0
    newer = mgr.create(working_dir="/newer")
    rows = mgr.list_active()
    assert [r["session_id"] for r in rows] == [newer.session_id, older.session_id]
    assert {r["session_id"]: r for r in rows}[older.session_id]["working_dir"] == "/older"
    for row in rows:
        assert {
            "session_id",
            "working_dir",
            "created_at",
            "last_touched",
            "event_count",
        } <= row.keys()


def test_manager_clear_drops_everything() -> None:
    mgr = OtterSessionManager(ttl=None)
    a = mgr.create()
    b = mgr.create()
    mgr.clear()
    assert mgr.list_ids() == []
    # Tear-down ran on every session.
    assert a.cancel.is_cancelled is True
    assert b.cancel.is_cancelled is True


def test_module_exports_session_manager() -> None:
    """The new types are part of the public ``__all__`` surface."""
    assert "OtterSessionManager" in otter_server.__all__
    assert "DEFAULT_SESSION_TTL" in otter_server.__all__
    assert OtterSessionManager.__name__ == "OtterSessionManager"
    assert DEFAULT_SESSION_TTL == 3600.0


# ---------------------------------------------------------------------------
# OtterServer constructor wiring
# ---------------------------------------------------------------------------


def test_server_builds_default_manager() -> None:
    """``OtterServer(...)`` auto-builds a manager with ``DEFAULT_SESSION_TTL``."""
    srv = OtterServer(agent_factory=None, host="127.0.0.1", port=0)
    assert isinstance(srv.session_manager, OtterSessionManager)
    assert srv.session_manager.ttl == DEFAULT_SESSION_TTL


def test_server_accepts_explicit_manager() -> None:
    """A test-built manager flows through as ``server.session_manager``."""
    mgr = OtterSessionManager(ttl=42.0)
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=0,
        session_manager=mgr,
    )
    assert srv.session_manager is mgr
    # Sessions created via the server show up in the manager (and vice versa).
    state = srv.create_session(working_dir="/x")
    assert mgr.get(state.session_id) is state


def test_server_session_ttl_kwarg_threads_through() -> None:
    """``session_ttl=...`` reaches the auto-built manager."""
    srv = OtterServer(
        agent_factory=None, host="127.0.0.1", port=0, session_ttl=99.0
    )
    assert srv.session_manager.ttl == 99.0


# ---------------------------------------------------------------------------
# HTTP — GET /sessions
# ---------------------------------------------------------------------------


def test_get_sessions_returns_metadata_for_each(server: OtterServer) -> None:
    """``GET /sessions`` lists every active session with timestamps."""
    _, c1 = _http("POST", f"{_base_url(server)}/session", body={"working_dir": "/a"})
    _, c2 = _http("POST", f"{_base_url(server)}/session", body={"working_dir": "/b"})
    status, body = _http("GET", f"{_base_url(server)}/sessions")
    assert status == 200
    rows = body["sessions"]
    assert isinstance(rows, list)
    ids = {row["session_id"] for row in rows}
    assert ids == {c1["session_id"], c2["session_id"]}
    for row in rows:
        assert {
            "session_id",
            "working_dir",
            "created_at",
            "last_touched",
            "event_count",
        } <= row.keys()
        assert isinstance(row["last_touched"], (int, float))
        assert isinstance(row["created_at"], (int, float))


def test_get_sessions_empty_when_none_created(server: OtterServer) -> None:
    status, body = _http("GET", f"{_base_url(server)}/sessions")
    assert status == 200
    assert body == {"sessions": []}


# ---------------------------------------------------------------------------
# HTTP — DELETE /session/<id>
# ---------------------------------------------------------------------------


def test_delete_session_returns_204_and_evicts(server: OtterServer) -> None:
    _, c = _http("POST", f"{_base_url(server)}/session", body={})
    sid = c["session_id"]
    # Confirm it exists.
    assert sid in server.list_session_ids()
    req = urllib.request.Request(
        f"{_base_url(server)}/session/{sid}", method="DELETE"
    )
    resp = urllib.request.urlopen(req, timeout=3.0)
    assert resp.status == 204
    # Body must be empty for 204 (RFC 7230 §3.3.2).
    assert resp.read() == b""
    # Manager dropped it.
    assert sid not in server.list_session_ids()


def test_delete_session_unknown_returns_404(server: OtterServer) -> None:
    req = urllib.request.Request(
        f"{_base_url(server)}/session/nope", method="DELETE"
    )
    try:
        urllib.request.urlopen(req, timeout=3.0)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
        body = json.loads(exc.read())
        assert body == {"error": "session_not_found"}
    else:  # pragma: no cover - defensive
        pytest.fail("expected 404 from DELETE on unknown session")


def test_delete_session_wakes_subscribers(server: OtterServer) -> None:
    """An SSE consumer connected to a deleted session sees its stream close."""
    _, c = _http("POST", f"{_base_url(server)}/session", body={})
    sid = c["session_id"]
    state = server.get_session(sid)
    assert state is not None
    import queue

    q: queue.Queue[dict[str, Any] | None] = queue.Queue()
    server.subscribe(state)  # warm replay
    # Simulate an in-process subscriber on the same session.
    state.subscribers.append(q)

    # Delete via the public API.
    assert server.delete_session(sid) is True

    # The added subscriber must receive the sentinel ``None``.
    assert q.get(timeout=1.0) is None


# ---------------------------------------------------------------------------
# Multi-session round-trip — SSE streams must not cross-contaminate
# ---------------------------------------------------------------------------


def _read_sse_bytes(srv: OtterServer, sid: str, *, max_bytes: int = 8192) -> bytes:
    """Open ``/session/<id>/events`` over a raw socket; return one chunk."""
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
        # Stop when we've seen at least the user_message + result frames.
        if buf.count(b"\n\n") >= 2:
            break
    s.close()
    return buf


def test_two_sessions_sse_streams_do_not_cross_contaminate(
    server: OtterServer,
) -> None:
    """Open two sessions, fire prompts on each; SSE streams stay disjoint.

    Each session's agent stamps its own ``session_id`` into the result
    payload (see :class:`_SessionAwareAgent`). If session A's
    ``user_message`` or ``result`` ever lands on session B's events
    list, the per-session assertions below would fail.
    """
    _, ca = _http("POST", f"{_base_url(server)}/session", body={"working_dir": "/a"})
    _, cb = _http("POST", f"{_base_url(server)}/session", body={"working_dir": "/b"})
    sid_a, sid_b = ca["session_id"], cb["session_id"]

    # Fire prompts on both. Both run in parallel background threads.
    _http(
        "POST",
        f"{_base_url(server)}/session/{sid_a}/message",
        body={"text": "alpha-task"},
    )
    _http(
        "POST",
        f"{_base_url(server)}/session/{sid_b}/message",
        body={"text": "bravo-task"},
    )

    # Wait for both sessions to land their terminal ``result`` events.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        sa, sb = server.get_session(sid_a), server.get_session(sid_b)
        assert sa is not None and sb is not None
        if any(ev["event"] == "result" for ev in sa.events) and any(
            ev["event"] == "result" for ev in sb.events
        ):
            break
        time.sleep(0.02)

    sa, sb = server.get_session(sid_a), server.get_session(sid_b)
    assert sa is not None and sb is not None

    # Session A only ever saw alpha-task; B only ever saw bravo-task.
    a_user_msgs = [
        ev["data"]["text"] for ev in sa.events if ev["event"] == "user_message"
    ]
    b_user_msgs = [
        ev["data"]["text"] for ev in sb.events if ev["event"] == "user_message"
    ]
    assert a_user_msgs == ["alpha-task"]
    assert b_user_msgs == ["bravo-task"]

    # Session A's result output ends with sid_a (fake agent stamps it);
    # session B's with sid_b. If the streams crossed, the suffixes flip.
    a_result = next(ev for ev in sa.events if ev["event"] == "result")
    b_result = next(ev for ev in sb.events if ev["event"] == "result")
    assert a_result["data"]["output"] == f"alpha-task::{sid_a}"
    assert b_result["data"]["output"] == f"bravo-task::{sid_b}"

    # Per-session SSE replay buffers: independent ``state.events`` lists.
    # Anything in A's list must not appear in B's list and vice versa.
    a_ids = {id(ev) for ev in sa.events}
    b_ids = {id(ev) for ev in sb.events}
    assert a_ids.isdisjoint(b_ids)


def test_two_sessions_can_be_observed_in_parallel(
    server: OtterServer,
) -> None:
    """Two SSE consumers (one per session) each see only their own frames."""
    _, ca = _http("POST", f"{_base_url(server)}/session", body={})
    _, cb = _http("POST", f"{_base_url(server)}/session", body={})
    sid_a, sid_b = ca["session_id"], cb["session_id"]

    received: dict[str, bytes] = {}

    def _consume(sid: str) -> None:
        received[sid] = _read_sse_bytes(server, sid)

    ta = threading.Thread(target=_consume, args=(sid_a,), daemon=True)
    tb = threading.Thread(target=_consume, args=(sid_b,), daemon=True)
    ta.start()
    tb.start()
    time.sleep(0.1)  # let both subscribers attach

    _http(
        "POST",
        f"{_base_url(server)}/session/{sid_a}/message",
        body={"text": "alpha-prompt"},
    )
    _http(
        "POST",
        f"{_base_url(server)}/session/{sid_b}/message",
        body={"text": "bravo-prompt"},
    )

    ta.join(timeout=5.0)
    tb.join(timeout=5.0)
    raw_a = received.get(sid_a, b"").decode("utf-8", "replace")
    raw_b = received.get(sid_b, b"").decode("utf-8", "replace")

    # Each stream must mention its own prompt and never the other's.
    assert "alpha-prompt" in raw_a
    assert "bravo-prompt" not in raw_a
    assert "bravo-prompt" in raw_b
    assert "alpha-prompt" not in raw_b


def test_concurrent_session_creates_dont_serialize() -> None:
    """``create_session`` from many threads doesn't deadlock or drop ids.

    Smoke-tests that the manager's lock is fine-grained: 32 threads all
    creating sessions in parallel must produce 32 distinct ids without
    any corruption of the underlying dict.
    """
    srv = OtterServer(agent_factory=None, host="127.0.0.1", port=0)
    ids: list[str] = []
    lock = threading.Lock()

    def _spawn() -> None:
        state = srv.create_session(working_dir="/x")
        with lock:
            ids.append(state.session_id)

    threads = [threading.Thread(target=_spawn) for _ in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert len(ids) == 32
    assert len(set(ids)) == 32
    assert set(srv.list_session_ids()) == set(ids)


# ---------------------------------------------------------------------------
# TTL eviction over the HTTP surface
# ---------------------------------------------------------------------------


def test_idle_session_is_evicted_via_get_sessions() -> None:
    """A session past its TTL drops out of ``GET /sessions``."""
    clock = [1000.0]
    mgr = OtterSessionManager(ttl=60.0, clock=lambda: clock[0])
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=0,
        session_manager=mgr,
    )
    srv.start(blocking=False)
    try:
        _, c = _http("POST", f"{_base_url(srv)}/session", body={})
        sid = c["session_id"]
        # Move clock past the TTL window without touching the session.
        clock[0] = 9_999.0
        status, body = _http("GET", f"{_base_url(srv)}/sessions")
        assert status == 200
        assert body == {"sessions": []}
        # And the session id is gone from the live map.
        assert sid not in srv.list_session_ids()
    finally:
        srv.shutdown()


def test_get_sessions_keeps_active_session_alive() -> None:
    """Looking a session up via the HTTP surface refreshes ``last_touched``."""
    clock = [1000.0]
    mgr = OtterSessionManager(ttl=60.0, clock=lambda: clock[0])
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=0,
        session_manager=mgr,
    )
    srv.start(blocking=False)
    try:
        _, c = _http("POST", f"{_base_url(srv)}/session", body={})
        sid = c["session_id"]
        # Advance the clock 30s — still inside TTL — and bump via GET.
        clock[0] = 1030.0
        status, _ = _http("GET", f"{_base_url(srv)}/session/{sid}")
        assert status == 200
        # Advance another 30s — would be past 60s without the bump.
        clock[0] = 1060.0
        status, body = _http("GET", f"{_base_url(srv)}/sessions")
        assert status == 200
        assert any(row["session_id"] == sid for row in body["sessions"])
    finally:
        srv.shutdown()

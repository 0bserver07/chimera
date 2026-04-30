"""Tests for :mod:`chimera.ferret.cloud_bridge`.

The bridge is a thin urllib client. We stand up a minimal stdlib
:class:`http.server.ThreadingHTTPServer` to play the role of the remote
UI and exercise:

* **Handshake.** ``POST /bridge/handshake`` returns a bridge id; the
  client caches it for subsequent calls.
* **Round-trip.** Inbound messages from the mock remote land in the
  configured ``inbound_handler``; outbound events posted by the agent
  reach the remote in order.
* **Auth failure.** A 401 from the remote raises
  :class:`BridgeAuthError` from :meth:`CloudBridge.connect` and stops
  the poll loop fatally.
* **Reconnect.** Transient transport errors trigger backoff-and-retry
  rather than tearing the bridge down.
* **Token resolution.** ``$FERRET_BRIDGE_TOKEN`` is honoured when the
  caller doesn't pass an explicit ``--bridge-token``.

The tests stay stdlib-only — no httpx, no requests — to match the
production module's zero-dependency stance.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from chimera.ferret import cloud_bridge as cb
from chimera.ferret.cloud_bridge import (
    BridgeAuthError,
    BridgeConfig,
    BridgeError,
    CloudBridge,
    InboundMessage,
    build_bridge_from_args,
    run_bridge,
)


# ---------------------------------------------------------------------------
# Mock remote bridge server
# ---------------------------------------------------------------------------


class _MockRemoteState:
    """Mutable state shared between the mock server and the test body."""

    def __init__(
        self,
        *,
        expected_token: str = "secret-bridge-token",
        bridge_id: str = "br-test-001",
        handshake_status: int = 200,
        poll_status: int = 200,
        event_status: int = 200,
        force_handshake_count_status: dict[int, int] | None = None,
    ) -> None:
        self.expected_token = expected_token
        self.bridge_id = bridge_id
        self.handshake_status = handshake_status
        self.poll_status = poll_status
        self.event_status = event_status
        # Map of N-th call -> override status (1-indexed). Lets a test
        # simulate a transient failure on the first poll without
        # affecting subsequent polls.
        self.force_handshake_count_status = force_handshake_count_status or {}
        self.handshake_calls = 0
        self.poll_calls = 0
        # Pending messages queued for the next poll. Populated by the
        # test body; drained one batch per poll request.
        self.pending: list[dict[str, Any]] = []
        self.received_events: list[dict[str, Any]] = []
        # Per-call status overrides for poll (count -> status).
        self.poll_status_by_call: dict[int, int] = {}
        self.lock = threading.Lock()


def _make_handler(state: _MockRemoteState) -> type[BaseHTTPRequestHandler]:
    """Build a :class:`BaseHTTPRequestHandler` subclass closed over *state*.

    Mirrors the closure pattern used by
    :func:`chimera.otter.server.OtterServer._build_handler_class`.
    """

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Silence stderr during tests.
            return

        def _check_auth(self) -> bool:
            got = self.headers.get("Authorization", "")
            expected = f"Bearer {state.expected_token}"
            if got != expected:
                self._send_json(401, {"error": "unauthorized"})
                return False
            return True

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b""
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}

        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            if not self._check_auth():
                return
            path = self.path.split("?", 1)[0]
            if path == "/bridge/handshake":
                with state.lock:
                    state.handshake_calls += 1
                    status = state.force_handshake_count_status.get(
                        state.handshake_calls, state.handshake_status
                    )
                if status != 200:
                    self._send_json(status, {"error": "handshake_failed"})
                    return
                self._read_json()  # drain
                self._send_json(200, {"bridge_id": state.bridge_id})
                return
            if path.startswith("/bridge/") and path.endswith("/event"):
                body = self._read_json()
                with state.lock:
                    state.received_events.append(body)
                if state.event_status >= 300:
                    self._send_json(state.event_status, {"error": "boom"})
                    return
                self._send_json(state.event_status, {"ok": True})
                return
            self._send_json(404, {"error": "not_found", "path": path})

        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            if not self._check_auth():
                return
            path = self.path.split("?", 1)[0]
            if path.startswith("/bridge/") and path.endswith("/poll"):
                with state.lock:
                    state.poll_calls += 1
                    status = state.poll_status_by_call.get(
                        state.poll_calls, state.poll_status
                    )
                    if status == 200:
                        msgs = state.pending[:]
                        state.pending.clear()
                    else:
                        msgs = []
                if status != 200:
                    self._send_json(status, {"error": "poll_failed"})
                    return
                self._send_json(200, {"messages": msgs})
                return
            self._send_json(404, {"error": "not_found", "path": path})

    return _Handler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def remote_state() -> _MockRemoteState:
    """Fresh remote-server state per test."""
    return _MockRemoteState()


@pytest.fixture()
def remote(
    remote_state: _MockRemoteState,
) -> Iterator[tuple[str, _MockRemoteState]]:
    """Spin up the mock remote on an OS-chosen port."""
    handler_cls = _make_handler(remote_state)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base_url, remote_state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2.0)


@pytest.fixture()
def collected_messages() -> list[InboundMessage]:
    """Sink for inbound messages captured by the test handler."""
    return []


@pytest.fixture()
def handler(
    collected_messages: list[InboundMessage],
) -> "Any":
    """A handler that records every inbound message."""

    def _handler(msg: InboundMessage) -> None:
        collected_messages.append(msg)

    return _handler


def _make_bridge(
    remote_url: str,
    handler: Any,
    *,
    token: str = "secret-bridge-token",
    poll_interval: float = 0.05,
    request_timeout: float = 2.0,
) -> CloudBridge:
    """Construct a :class:`CloudBridge` aimed at the mock remote."""
    config = BridgeConfig(
        remote_url=remote_url,
        token=token,
        poll_interval=poll_interval,
        request_timeout=request_timeout,
        max_backoff=0.2,
    )
    return CloudBridge(config, handler)


# ---------------------------------------------------------------------------
# Defaults / config
# ---------------------------------------------------------------------------


def test_default_remote_url_is_invalid_placeholder() -> None:
    """Trademark guard: the default remote must NOT resolve to a real host.

    RFC 2606 reserves ``.invalid`` so the placeholder can never collide
    with a production remote.
    """
    assert cb.DEFAULT_REMOTE_URL.endswith(".invalid")
    assert "https://" in cb.DEFAULT_REMOTE_URL
    # And we must not name the upstream brand or hardcode their cloud.
    upstream = ("chatgpt", "openai.com", "codex")
    for needle in upstream:
        assert needle not in cb.DEFAULT_REMOTE_URL.lower()


def test_resolve_token_prefers_explicit_value() -> None:
    config = BridgeConfig(token="explicit")
    assert config.resolve_token({"FERRET_BRIDGE_TOKEN": "env"}) == "explicit"


def test_resolve_token_falls_back_to_env() -> None:
    config = BridgeConfig(token=None)
    assert config.resolve_token({"FERRET_BRIDGE_TOKEN": "env-tok"}) == "env-tok"


def test_resolve_token_raises_when_unset() -> None:
    config = BridgeConfig(token=None)
    with pytest.raises(BridgeAuthError):
        config.resolve_token({})


def test_normalised_remote_strips_trailing_slash() -> None:
    config = BridgeConfig(remote_url="https://example.test/api/")
    assert config.normalised_remote() == "https://example.test/api"


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------


def test_connect_returns_bridge_id(
    remote: tuple[str, _MockRemoteState], handler: Any
) -> None:
    base_url, state = remote
    bridge = _make_bridge(base_url, handler)
    bridge_id = bridge.connect()
    assert bridge_id == state.bridge_id
    assert bridge.bridge_id == state.bridge_id
    assert state.handshake_calls == 1


def test_connect_auth_failure_raises(
    remote: tuple[str, _MockRemoteState], handler: Any
) -> None:
    base_url, _state = remote
    bridge = _make_bridge(base_url, handler, token="wrong-token")
    with pytest.raises(BridgeAuthError):
        bridge.connect()


def test_connect_handshake_non_200_raises(
    remote: tuple[str, _MockRemoteState], handler: Any
) -> None:
    base_url, state = remote
    state.handshake_status = 500
    bridge = _make_bridge(base_url, handler)
    with pytest.raises(BridgeError) as exc:
        bridge.connect()
    assert "handshake failed" in str(exc.value)


def test_connect_missing_bridge_id_raises(
    remote: tuple[str, _MockRemoteState], handler: Any
) -> None:
    """A 200 with no ``bridge_id`` is still a protocol error."""
    base_url, state = remote
    # Switch to a custom handler that returns 200 with empty body.
    state.bridge_id = ""  # mock server will send empty string
    bridge = _make_bridge(base_url, handler)
    with pytest.raises(BridgeError) as exc:
        bridge.connect()
    assert "bridge_id" in str(exc.value)


def test_connect_resolves_token_from_env(
    remote: tuple[str, _MockRemoteState],
    handler: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url, state = remote
    monkeypatch.setenv("FERRET_BRIDGE_TOKEN", state.expected_token)
    config = BridgeConfig(
        remote_url=base_url,
        token=None,
        poll_interval=0.05,
        request_timeout=2.0,
    )
    bridge = CloudBridge(config, handler)
    assert bridge.connect() == state.bridge_id


# ---------------------------------------------------------------------------
# Round-trip: inbound messages + outbound events
# ---------------------------------------------------------------------------


def test_inbound_messages_dispatch_to_handler(
    remote: tuple[str, _MockRemoteState],
    handler: Any,
    collected_messages: list[InboundMessage],
) -> None:
    base_url, state = remote
    bridge = _make_bridge(base_url, handler)
    bridge.connect()
    state.pending.append(
        {"message_id": "m1", "text": "hello ferret", "kind": "prompt"}
    )
    bridge.start()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline and not collected_messages:
            time.sleep(0.02)
    finally:
        bridge.stop()
    assert len(collected_messages) == 1
    msg = collected_messages[0]
    assert msg.message_id == "m1"
    assert msg.text == "hello ferret"
    assert msg.kind == "prompt"


def test_send_event_reaches_remote(
    remote: tuple[str, _MockRemoteState], handler: Any
) -> None:
    base_url, state = remote
    bridge = _make_bridge(base_url, handler)
    bridge.connect()
    bridge.send_event("loop_event", {"text": "hi"}, message_id="m1")
    bridge.send_event("result", {"output": "done"}, message_id="m1")
    assert len(state.received_events) == 2
    assert state.received_events[0]["type"] == "loop_event"
    assert state.received_events[0]["data"] == {"text": "hi"}
    assert state.received_events[0]["message_id"] == "m1"
    assert state.received_events[1]["type"] == "result"


def test_send_event_before_connect_raises(handler: Any) -> None:
    config = BridgeConfig(token="x")
    bridge = CloudBridge(config, handler)
    with pytest.raises(BridgeError):
        bridge.send_event("x", {})


def test_send_event_remote_5xx_raises(
    remote: tuple[str, _MockRemoteState], handler: Any
) -> None:
    base_url, state = remote
    state.event_status = 500
    bridge = _make_bridge(base_url, handler)
    bridge.connect()
    with pytest.raises(BridgeError):
        bridge.send_event("x", {})


# ---------------------------------------------------------------------------
# Auth-failure path on the poll loop
# ---------------------------------------------------------------------------


def test_poll_auth_failure_stops_loop(
    remote: tuple[str, _MockRemoteState],
    handler: Any,
) -> None:
    """A 401 mid-poll is fatal — the loop records the auth error and exits."""
    base_url, state = remote
    bridge = _make_bridge(base_url, handler)
    bridge.connect()
    # Force every subsequent poll to 401.
    state.poll_status = 401
    bridge.start()
    deadline = time.time() + 3.0
    while time.time() < deadline and bridge.running:
        time.sleep(0.02)
    bridge.stop()
    assert not bridge.running
    assert any(e.startswith("auth:") for e in bridge.errors), bridge.errors


# ---------------------------------------------------------------------------
# Reconnect / transient-error backoff
# ---------------------------------------------------------------------------


def test_poll_transient_500_then_recover(
    remote: tuple[str, _MockRemoteState],
    handler: Any,
    collected_messages: list[InboundMessage],
) -> None:
    """First poll returns 500 (transient); the second poll succeeds."""
    base_url, state = remote
    state.poll_status_by_call = {1: 500}
    state.pending.append({"message_id": "m1", "text": "after recover"})
    bridge = _make_bridge(base_url, handler)
    bridge.connect()
    bridge.start()
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and not collected_messages:
            time.sleep(0.05)
    finally:
        bridge.stop()
    assert len(collected_messages) == 1
    assert collected_messages[0].text == "after recover"
    # The bridge should have logged the transient error before recovering.
    assert any("poll failed" in e for e in bridge.errors), bridge.errors
    assert state.poll_calls >= 2


def test_poll_url_error_is_transient(
    handler: Any,
    collected_messages: list[InboundMessage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A :class:`urllib.error.URLError` mid-poll surfaces as a transient
    :class:`BridgeError` and the loop keeps trying.

    We patch :class:`urllib.request.OpenerDirector.open` so the very
    first poll raises, then yields the connection back to the underlying
    opener. The bridge should record one transient error in
    :attr:`CloudBridge.errors` and continue running.
    """
    config = BridgeConfig(
        remote_url="http://127.0.0.1:1",
        token="t",
        poll_interval=0.05,
        request_timeout=0.2,
        max_backoff=0.1,
    )
    bridge = CloudBridge(config, handler)
    bridge.bridge_id = "br-test-fake"  # skip the handshake
    bridge._token = "t"

    # Force every poll attempt to fail with URLError; the loop should
    # keep retrying without raising and without exiting.
    import urllib.error

    def _always_url_error(*_args: Any, **_kwargs: Any) -> Any:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(bridge._opener, "open", _always_url_error)
    bridge.start()
    time.sleep(0.5)
    assert bridge.running, bridge.errors
    bridge.stop()
    assert any("transport error" in e for e in bridge.errors), bridge.errors


# ---------------------------------------------------------------------------
# Argparse-style helper
# ---------------------------------------------------------------------------


class _Args:
    """Minimal stand-in for :class:`argparse.Namespace`."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_build_bridge_from_args_uses_defaults(handler: Any) -> None:
    args = _Args(remote_url=None, bridge_token=None)
    # No env, no explicit token — building works (token resolves lazily),
    # the default URL is the placeholder.
    bridge = build_bridge_from_args(args, handler)
    assert bridge._config.remote_url == cb.DEFAULT_REMOTE_URL
    assert bridge._config.client_id == "ferret"


def test_build_bridge_from_args_honours_overrides(handler: Any) -> None:
    args = _Args(
        remote_url="https://custom.test/path/",
        bridge_token="abc",
        client_id="ferret-test",
        poll_interval=0.5,
        request_timeout=10.0,
    )
    bridge = build_bridge_from_args(args, handler)
    assert bridge._config.normalised_remote() == "https://custom.test/path"
    assert bridge._config.token == "abc"
    assert bridge._config.client_id == "ferret-test"
    assert bridge._config.poll_interval == 0.5


def test_run_bridge_returns_2_on_auth_failure(
    remote: tuple[str, _MockRemoteState], handler: Any
) -> None:
    base_url, _state = remote
    args = _Args(
        remote_url=base_url,
        bridge_token="wrong",
        poll_interval=0.05,
        request_timeout=2.0,
    )
    rc = run_bridge(args, handler)
    assert rc == 2


def test_run_bridge_returns_1_on_connect_error(handler: Any) -> None:
    """Pointing at a closed port gives a transport-level connect error."""
    args = _Args(
        remote_url="http://127.0.0.1:1",
        bridge_token="t",
        poll_interval=0.05,
        request_timeout=0.5,
    )
    rc = run_bridge(args, handler)
    assert rc == 1


# ---------------------------------------------------------------------------
# Sanity: stop() is idempotent + start() before connect raises
# ---------------------------------------------------------------------------


def test_start_before_connect_raises(handler: Any) -> None:
    bridge = CloudBridge(BridgeConfig(token="t"), handler)
    with pytest.raises(BridgeError):
        bridge.start()


def test_stop_is_idempotent(handler: Any) -> None:
    bridge = CloudBridge(BridgeConfig(token="t"), handler)
    bridge.stop()
    bridge.stop()  # second call must not blow up


def test_url_helper_handles_missing_leading_slash(handler: Any) -> None:
    bridge = CloudBridge(
        BridgeConfig(remote_url="https://x.test", token="t"), handler
    )
    assert bridge._url("foo") == "https://x.test/foo"
    assert bridge._url("/foo") == "https://x.test/foo"


# Silence unused-import warnings for ``urllib.request`` (the test suite
# relies on it indirectly via the monkeypatched ``opener``).
_ = urllib.request

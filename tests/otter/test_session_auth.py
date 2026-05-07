"""Tests for B8 (wave-11) per-session bearer token auth.

The wave-9 :class:`OtterServer` shipped with a single shared bearer
token (``--auth-token``). Wave-11 B8 layers per-session tokens on top:

* Every ``POST /session`` issues a fresh ``session_token`` (returned in
  the response body, generated via :func:`secrets.token_urlsafe`).
* A session token authorizes only requests against its own
  ``/session/<id>/...`` subtree. It cannot create new sessions, list
  sessions, rotate, or touch other sessions.
* The master ``--auth-token`` is unchanged — it still authorizes
  every route. It is also the only token allowed to invoke
  ``POST /session/<id>/rotate-token``; presenting a session token to
  that route returns ``403 admin_only`` (not 401, because the request
  *is* authenticated, just under-privileged).
* Rotation invalidates the previous token immediately — the next
  request that still carries the old token gets ``401 unauthorized``.

Tests stay stdlib-only, mirroring :mod:`tests.otter.test_server`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator

import pytest

from chimera.otter.server import OtterServer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_MASTER_TOKEN = "master-secret"


@pytest.fixture()
def auth_server() -> Iterator[OtterServer]:
    """A server bound with the master ``--auth-token`` set.

    No agent factory is needed — these tests assert auth behaviour, not
    agent dispatch. Every request goes through ``_check_auth`` first,
    so the agent path is never exercised.
    """
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=0,
        auth_token=_MASTER_TOKEN,
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
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """Thin urllib wrapper. Returns ``(status, body)`` even on HTTP errors."""
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


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_session(srv: OtterServer) -> tuple[str, str]:
    """Create a session via the master token; return ``(session_id, session_token)``."""
    status, body = _http(
        "POST",
        f"{_base_url(srv)}/session",
        body={},
        headers=_bearer(_MASTER_TOKEN),
    )
    assert status == 201, body
    assert "session_id" in body
    assert "session_token" in body
    return body["session_id"], body["session_token"]


# ---------------------------------------------------------------------------
# 1. Sanity: master token still authorizes everything.
# ---------------------------------------------------------------------------


def test_master_token_works_for_all_sessions(auth_server: OtterServer) -> None:
    """The master ``--auth-token`` authorizes admin + every session.

    Sanity check that pre-B8 behaviour is preserved: a single
    ``Bearer <master>`` header continues to work for ``POST /session``,
    ``GET /session`` (list), ``GET /session/<id>`` (snapshot), and
    multi-session admin operations.
    """
    sid_a, _tok_a = _create_session(auth_server)
    sid_b, _tok_b = _create_session(auth_server)

    # GET /session/<id> for both sessions with the master token.
    for sid in (sid_a, sid_b):
        status, body = _http(
            "GET",
            f"{_base_url(auth_server)}/session/{sid}",
            headers=_bearer(_MASTER_TOKEN),
        )
        assert status == 200
        assert body["session_id"] == sid

    # GET /sessions admin route works under master token.
    status, body = _http(
        "GET",
        f"{_base_url(auth_server)}/sessions",
        headers=_bearer(_MASTER_TOKEN),
    )
    assert status == 200
    sids = {s["session_id"] for s in body["sessions"]}
    assert sids == {sid_a, sid_b}


# ---------------------------------------------------------------------------
# 2. Session token only authorizes its own session.
# ---------------------------------------------------------------------------


def test_session_token_only_works_for_own_session(
    auth_server: OtterServer,
) -> None:
    """Session A's token cannot read session B."""
    sid_a, tok_a = _create_session(auth_server)
    sid_b, _tok_b = _create_session(auth_server)

    # Session A's token MUST work for session A.
    status, body = _http(
        "GET",
        f"{_base_url(auth_server)}/session/{sid_a}",
        headers=_bearer(tok_a),
    )
    assert status == 200
    assert body["session_id"] == sid_a

    # Session A's token MUST NOT work for session B.
    status, body = _http(
        "GET",
        f"{_base_url(auth_server)}/session/{sid_b}",
        headers=_bearer(tok_a),
    )
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_session_token_works_for_own_session(auth_server: OtterServer) -> None:
    """Session A's token authorizes /session/<sid_a>/... messaging routes."""
    sid_a, tok_a = _create_session(auth_server)

    # GET /session/<id> snapshot.
    status, body = _http(
        "GET",
        f"{_base_url(auth_server)}/session/{sid_a}",
        headers=_bearer(tok_a),
    )
    assert status == 200
    assert body["session_id"] == sid_a

    # POST /session/<id>/cancel — session-scoped operation.
    # No agent_factory is configured, so cancel is a no-op gate, but
    # auth gating runs first; we want to confirm that the session
    # token reaches the handler (returns 204).
    status, _body = _http(
        "POST",
        f"{_base_url(auth_server)}/session/{sid_a}/cancel",
        body={},
        headers=_bearer(tok_a),
    )
    assert status == 204


# ---------------------------------------------------------------------------
# 3. POST /session response shape.
# ---------------------------------------------------------------------------


def test_create_session_returns_token(auth_server: OtterServer) -> None:
    """``POST /session`` response body includes a per-session token."""
    status, body = _http(
        "POST",
        f"{_base_url(auth_server)}/session",
        body={"working_dir": "/tmp/abc"},
        headers=_bearer(_MASTER_TOKEN),
    )
    assert status == 201
    # Pre-existing fields must still be present.
    assert isinstance(body["session_id"], str) and body["session_id"]
    assert body["working_dir"] == "/tmp/abc"
    assert "created_at" in body
    # New B8 field.
    assert isinstance(body["session_token"], str)
    # ``secrets.token_urlsafe(32)`` produces ~43 url-safe chars.
    assert len(body["session_token"]) >= 32
    assert body["session_token"] != _MASTER_TOKEN


def test_create_session_tokens_are_distinct(auth_server: OtterServer) -> None:
    """Each ``POST /session`` issues a freshly random token."""
    _, tok_a = _create_session(auth_server)
    _, tok_b = _create_session(auth_server)
    assert tok_a != tok_b


# ---------------------------------------------------------------------------
# 4. Rotate-token admin gate.
# ---------------------------------------------------------------------------


def test_rotate_token_admin_only(auth_server: OtterServer) -> None:
    """Session token cannot rotate (403); master token can (200)."""
    sid_a, tok_a = _create_session(auth_server)

    # Session token presented for rotate-token: 403 admin_only.
    # ``_check_auth`` accepts the session token (the request is
    # authenticated against /session/<sid_a>/...), but the rotate-token
    # handler enforces master-only and returns 403.
    status, body = _http(
        "POST",
        f"{_base_url(auth_server)}/session/{sid_a}/rotate-token",
        body={},
        headers=_bearer(tok_a),
    )
    assert status == 403
    assert body == {"error": "admin_only"}

    # Master token: 200 with a fresh session_token.
    status, body = _http(
        "POST",
        f"{_base_url(auth_server)}/session/{sid_a}/rotate-token",
        body={},
        headers=_bearer(_MASTER_TOKEN),
    )
    assert status == 200
    assert isinstance(body["session_token"], str)
    assert body["session_token"] != tok_a


def test_rotate_token_unknown_session_is_404(auth_server: OtterServer) -> None:
    """Rotating a non-existent session id returns 404."""
    status, body = _http(
        "POST",
        f"{_base_url(auth_server)}/session/does-not-exist/rotate-token",
        body={},
        headers=_bearer(_MASTER_TOKEN),
    )
    assert status == 404
    assert body == {"error": "session_not_found"}


# ---------------------------------------------------------------------------
# 5. Rotated token invalidates the old one.
# ---------------------------------------------------------------------------


def test_rotated_token_invalidates_old(auth_server: OtterServer) -> None:
    """After rotation the old session token is rejected; new one works."""
    sid_a, tok_a = _create_session(auth_server)

    # Sanity: old token works pre-rotation.
    status, _body = _http(
        "GET",
        f"{_base_url(auth_server)}/session/{sid_a}",
        headers=_bearer(tok_a),
    )
    assert status == 200

    # Rotate.
    status, body = _http(
        "POST",
        f"{_base_url(auth_server)}/session/{sid_a}/rotate-token",
        body={},
        headers=_bearer(_MASTER_TOKEN),
    )
    assert status == 200
    new_tok = body["session_token"]
    assert new_tok != tok_a

    # Old token is now invalid (401).
    status, body = _http(
        "GET",
        f"{_base_url(auth_server)}/session/{sid_a}",
        headers=_bearer(tok_a),
    )
    assert status == 401
    assert body == {"error": "unauthorized"}

    # New token works.
    status, body = _http(
        "GET",
        f"{_base_url(auth_server)}/session/{sid_a}",
        headers=_bearer(new_tok),
    )
    assert status == 200
    assert body["session_id"] == sid_a

    # Master token still works.
    status, body = _http(
        "GET",
        f"{_base_url(auth_server)}/session/{sid_a}",
        headers=_bearer(_MASTER_TOKEN),
    )
    assert status == 200


# ---------------------------------------------------------------------------
# 6. Session token cannot reach admin-level routes.
# ---------------------------------------------------------------------------


def test_session_token_cannot_create_session(auth_server: OtterServer) -> None:
    """``POST /session`` (create) is admin-only.

    Session tokens are scoped to ``/session/<their-id>/...``; presenting
    a session token to ``POST /session`` (no id in path) must fail with
    ``401 unauthorized``.
    """
    _sid_a, tok_a = _create_session(auth_server)
    status, body = _http(
        "POST",
        f"{_base_url(auth_server)}/session",
        body={},
        headers=_bearer(tok_a),
    )
    assert status == 401
    assert body == {"error": "unauthorized"}


def test_session_token_cannot_list_sessions(auth_server: OtterServer) -> None:
    """``GET /sessions`` listing is admin-only."""
    _sid_a, tok_a = _create_session(auth_server)
    status, body = _http(
        "GET",
        f"{_base_url(auth_server)}/sessions",
        headers=_bearer(tok_a),
    )
    assert status == 401
    assert body == {"error": "unauthorized"}

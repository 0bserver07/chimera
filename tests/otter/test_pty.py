"""Tests for ``chimera.otter.pty`` (W14-2 — PTY subprocesses)."""
from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator

import pytest

from chimera.otter import pty as pty_mod

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="PTY routes are POSIX-only",
)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def test_is_supported_matches_platform() -> None:
    assert pty_mod.is_supported() is (sys.platform != "win32")


# ---------------------------------------------------------------------------
# PtyManager — start / read / stop
# ---------------------------------------------------------------------------


@pytest.fixture()
def manager() -> Iterator[pty_mod.PtyManager]:
    m = pty_mod.PtyManager()
    yield m
    m.shutdown_all()


@POSIX_ONLY
def test_start_runs_command_and_captures_output(
    manager: pty_mod.PtyManager,
) -> None:
    session = manager.start("echo hello-pty")
    # Drain output for up to ~2s.
    deadline = time.time() + 2.0
    out = b""
    while time.time() < deadline:
        chunk, exit_code = manager.read(session.pty_id)
        out += chunk
        if exit_code is not None and not chunk:
            break
        time.sleep(0.05)
    assert b"hello-pty" in out


@POSIX_ONLY
def test_start_rejects_empty_command(manager: pty_mod.PtyManager) -> None:
    with pytest.raises(ValueError):
        manager.start("")


@POSIX_ONLY
def test_start_with_list_command(manager: pty_mod.PtyManager) -> None:
    session = manager.start(["/bin/sh", "-c", "echo list-cmd"])
    deadline = time.time() + 2.0
    out = b""
    while time.time() < deadline:
        chunk, exit_code = manager.read(session.pty_id)
        out += chunk
        if exit_code is not None and not chunk:
            break
        time.sleep(0.05)
    assert b"list-cmd" in out


@POSIX_ONLY
def test_write_then_read_round_trip(manager: pty_mod.PtyManager) -> None:
    session = manager.start(["/bin/sh", "-c", "read x; echo got:$x"])
    manager.write(session.pty_id, "alpha\n")
    deadline = time.time() + 3.0
    out = b""
    while time.time() < deadline:
        chunk, exit_code = manager.read(session.pty_id)
        out += chunk
        if exit_code is not None and not chunk:
            break
        time.sleep(0.05)
    assert b"got:alpha" in out


@POSIX_ONLY
def test_stop_returns_exit_code(manager: pty_mod.PtyManager) -> None:
    session = manager.start("sleep 30")
    code = manager.stop(session.pty_id, timeout=2.0)
    assert code is not None


@POSIX_ONLY
def test_stop_returns_none_for_missing(manager: pty_mod.PtyManager) -> None:
    assert manager.stop("ghost") is None


@POSIX_ONLY
def test_get_returns_none_for_missing(manager: pty_mod.PtyManager) -> None:
    assert manager.get("ghost") is None


@POSIX_ONLY
def test_list_ids_reports_active(manager: pty_mod.PtyManager) -> None:
    session = manager.start("sleep 5")
    assert session.pty_id in manager.list_ids()
    manager.stop(session.pty_id, timeout=1.0)


@POSIX_ONLY
def test_resize_updates_dims(manager: pty_mod.PtyManager) -> None:
    session = manager.start("sleep 5")
    manager.resize(session.pty_id, 100, 40)
    s = manager.get(session.pty_id)
    assert s is not None
    assert s.cols == 100
    assert s.rows == 40
    manager.stop(session.pty_id, timeout=1.0)


@POSIX_ONLY
def test_write_unknown_pty_raises_keyerror(
    manager: pty_mod.PtyManager,
) -> None:
    with pytest.raises(KeyError):
        manager.write("nope", "data")


@POSIX_ONLY
def test_read_unknown_pty_raises_keyerror(
    manager: pty_mod.PtyManager,
) -> None:
    with pytest.raises(KeyError):
        manager.read("nope")


@POSIX_ONLY
def test_resize_unknown_pty_raises_keyerror(
    manager: pty_mod.PtyManager,
) -> None:
    with pytest.raises(KeyError):
        manager.resize("nope", 80, 24)


@POSIX_ONLY
def test_subscribe_receives_chunk(manager: pty_mod.PtyManager) -> None:
    session = manager.start("echo subscribe-probe")
    queue = manager.subscribe(session.pty_id)
    deadline = time.time() + 2.0
    found = False
    while time.time() < deadline:
        try:
            chunk = queue.get(timeout=0.5)
        except Exception:  # noqa: BLE001
            continue
        if b"subscribe-probe" in chunk:
            found = True
            break
        if not chunk:
            break
    assert found


@POSIX_ONLY
def test_subscribe_unknown_pty_raises_keyerror(
    manager: pty_mod.PtyManager,
) -> None:
    with pytest.raises(KeyError):
        manager.subscribe("nope")


@POSIX_ONLY
def test_unsubscribe_silent_for_unknown(
    manager: pty_mod.PtyManager,
) -> None:
    # Should not raise even when pty_id is unknown.
    manager.unsubscribe("nope", object())


# ---------------------------------------------------------------------------
# HTTP routes — run a real otter server and exercise the PTY endpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
def http_server() -> Iterator[tuple[str, str]]:
    """Start an OtterServer and create one session; yield (base_url, session_id)."""
    from chimera.otter.server import OtterServer

    server = OtterServer(agent_factory=None, host="127.0.0.1", port=0)
    server.start(blocking=False)
    try:
        # Inspect the bound port.
        assert server._httpd is not None  # noqa: SLF001
        port = server._httpd.server_address[1]  # noqa: SLF001
        base = f"http://127.0.0.1:{port}"
        # Create a session.
        from urllib.request import Request, urlopen

        req = Request(
            f"{base}/session",
            data=json.dumps({"working_dir": ""}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        session_id = payload["session_id"]
        yield base, session_id
    finally:
        # Reap any running PTYs first to avoid blocking shutdown.
        if server._pty_manager is not None:  # noqa: SLF001
            server.pty_manager.shutdown_all()
        server.shutdown()


@POSIX_ONLY
def test_pty_start_route_returns_pty_id(
    http_server: tuple[str, str],
) -> None:
    from urllib.request import Request, urlopen

    base, session_id = http_server
    req = Request(
        f"{base}/session/{session_id}/pty/start",
        data=json.dumps({"command": "sleep 5"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    assert "pty_id" in payload
    assert payload["cols"] == 80
    assert payload["rows"] == 24


@POSIX_ONLY
def test_pty_start_missing_command_returns_400(
    http_server: tuple[str, str],
) -> None:
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    base, session_id = http_server
    req = Request(
        f"{base}/session/{session_id}/pty/start",
        data=json.dumps({}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(HTTPError) as exc:
        urlopen(req)
    assert exc.value.code == 400


@POSIX_ONLY
def test_pty_output_route_drains_stdout(
    http_server: tuple[str, str],
) -> None:
    from urllib.request import Request, urlopen

    base, session_id = http_server
    # Start
    req = Request(
        f"{base}/session/{session_id}/pty/start",
        data=json.dumps({"command": "echo route-probe"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req) as resp:
        pty_id = json.loads(resp.read())["pty_id"]
    # Poll output for up to 2s.
    deadline = time.time() + 2.0
    seen = ""
    while time.time() < deadline:
        with urlopen(f"{base}/session/{session_id}/pty/{pty_id}/output") as resp:
            payload = json.loads(resp.read())
        seen += payload["data"]
        if "route-probe" in seen:
            break
        time.sleep(0.05)
    assert "route-probe" in seen


@POSIX_ONLY
def test_pty_unknown_session_returns_404(
    http_server: tuple[str, str],
) -> None:
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    base, _ = http_server
    req = Request(
        f"{base}/session/no-such-session/pty/start",
        data=json.dumps({"command": "echo x"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(HTTPError) as exc:
        urlopen(req)
    assert exc.value.code == 404


@POSIX_ONLY
def test_pty_output_unknown_pty_returns_404(
    http_server: tuple[str, str],
) -> None:
    from urllib.error import HTTPError
    from urllib.request import urlopen

    base, session_id = http_server
    with pytest.raises(HTTPError) as exc:
        urlopen(f"{base}/session/{session_id}/pty/ghost/output")
    assert exc.value.code == 404


@POSIX_ONLY
def test_pty_input_route_writes(
    http_server: tuple[str, str],
) -> None:
    from urllib.request import Request, urlopen

    base, session_id = http_server
    # Start a shell that reads one line and echoes it back.
    req = Request(
        f"{base}/session/{session_id}/pty/start",
        data=json.dumps(
            {"command": "/bin/sh -c 'read x; echo got:$x'"}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req) as resp:
        pty_id = json.loads(resp.read())["pty_id"]
    # Send input.
    inp = Request(
        f"{base}/session/{session_id}/pty/{pty_id}/input",
        data=json.dumps({"data": "echo-line\n"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(inp) as resp:
        body = json.loads(resp.read())
    assert body["written"] > 0
    # Poll output.
    deadline = time.time() + 3.0
    seen = ""
    while time.time() < deadline:
        with urlopen(f"{base}/session/{session_id}/pty/{pty_id}/output") as resp:
            payload = json.loads(resp.read())
        seen += payload["data"]
        if "got:echo-line" in seen:
            break
        time.sleep(0.05)
    assert "got:echo-line" in seen


@POSIX_ONLY
def test_pty_resize_route_updates_dims(
    http_server: tuple[str, str],
) -> None:
    from urllib.request import Request, urlopen

    base, session_id = http_server
    req = Request(
        f"{base}/session/{session_id}/pty/start",
        data=json.dumps({"command": "sleep 5"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req) as resp:
        pty_id = json.loads(resp.read())["pty_id"]
    rs = Request(
        f"{base}/session/{session_id}/pty/{pty_id}/resize",
        data=json.dumps({"cols": 132, "rows": 50}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(rs) as resp:
        body = json.loads(resp.read())
    assert body == {"cols": 132, "rows": 50}


@POSIX_ONLY
def test_pty_stop_route_terminates(
    http_server: tuple[str, str],
) -> None:
    from urllib.request import Request, urlopen

    base, session_id = http_server
    req = Request(
        f"{base}/session/{session_id}/pty/start",
        data=json.dumps({"command": "sleep 30"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req) as resp:
        pty_id = json.loads(resp.read())["pty_id"]
    stop = Request(
        f"{base}/session/{session_id}/pty/{pty_id}/stop",
        data=b"",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(stop) as resp:
        body = json.loads(resp.read())
    assert "exit_code" in body

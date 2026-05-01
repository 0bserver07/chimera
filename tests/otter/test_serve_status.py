"""Tests for ``chimera otter|ferret serve status/stop`` (server-mgmt).

These tests synthesise PID-file fixtures under ``tmp_path`` and exercise
:mod:`chimera.otter.server_pidfile` plus the CLI management dispatcher
(:func:`chimera.otter.cli._dispatch_serve_management`) without actually
spawning HTTP servers — every signal call is intercepted via a fake
``kill`` callable that records the targets.

Coverage:

* PID-file write/read/list/remove round-trip + SHA-256 token hashing.
* ``stop_server`` happy path: SIGTERM only, no SIGKILL escalation.
* ``stop_server`` slow shutdown: SIGTERM → wait timeout → SIGKILL.
* ``stop_server`` for already-dead pid (idempotent + cleans pidfile).
* ``stop_all`` filters by prefix and (optionally) port.
* CLI ``status`` lists every backgrounded server with one line per pid.
* CLI ``stop --all`` graceful-stops every matching server.
* CLI ``stop`` (no args) auto-targets the lone running server.
* CLI ``stop`` errors when multiple servers are running and no
  --port / --all is supplied.

Stdlib only: no httpx, no requests, no real subprocesses.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
from pathlib import Path
from typing import Any

import pytest

from chimera.otter import cli as otter_cli
from chimera.otter import server_pidfile


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeKill:
    """Records every ``(pid, sig)`` call without actually signalling."""

    def __init__(self, *, alive_after: dict[int, list[int]] | None = None) -> None:
        # ``alive_after[pid]`` is a list of "calls remaining before the
        # process appears dead". Each ``__call__`` decrements the counter
        # for that pid; once <= 0 the next ``alive`` check returns False.
        self.calls: list[tuple[int, int]] = []
        self._alive_after: dict[int, int] = {}
        if alive_after is not None:
            for pid, count in alive_after.items():
                self._alive_after[pid] = (
                    count[0] if isinstance(count, list) else int(count)
                )

    def __call__(self, pid: int, sig: int) -> None:
        self.calls.append((pid, sig))
        if sig == 0:
            # ``kill(pid, 0)`` is the alive probe used by ``process_alive``.
            count = self._alive_after.get(pid)
            if count is None:
                return  # default: alive forever
            if count <= 0:
                raise ProcessLookupError(f"no such process {pid}")
            self._alive_after[pid] = count - 1


class _FakeSleep:
    """Records every ``sleep(seconds)`` call."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(float(seconds))


# ---------------------------------------------------------------------------
# server_pidfile primitives
# ---------------------------------------------------------------------------


def test_pidfile_path_uses_prefix_and_port(tmp_path: Path) -> None:
    """``pidfile_path`` joins prefix + port + base_dir into one file."""
    p = server_pidfile.pidfile_path("otter", 5173, base_dir=tmp_path)
    assert p == tmp_path / "otter-5173.pid"


def test_hash_auth_token_returns_sha256_or_none() -> None:
    """The plaintext token is hashed; ``None`` / empty stay ``None``."""
    assert server_pidfile.hash_auth_token(None) is None
    assert server_pidfile.hash_auth_token("") is None
    h = server_pidfile.hash_auth_token("super-secret")
    assert h is not None
    assert h.startswith("sha256:")
    assert len(h.split(":", 1)[1]) == 64  # SHA-256 hex


def test_write_and_read_pidfile_roundtrip(tmp_path: Path) -> None:
    """Writing then reading the pidfile recovers the same payload."""
    path = server_pidfile.write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=5173,
        pid=99999,
        auth_token="abc",
        scheme="https",
        started_at=1000.0,
        base_dir=tmp_path,
    )
    assert path == tmp_path / "otter-5173.pid"
    raw = json.loads(path.read_text())
    assert raw["pid"] == 99999
    assert raw["port"] == 5173
    assert raw["prefix"] == "otter"
    assert raw["host"] == "127.0.0.1"
    assert raw["scheme"] == "https"
    assert raw["started_at"] == 1000.0
    assert raw["auth_token_hash"].startswith("sha256:")

    parsed = server_pidfile.read_pidfile(path)
    assert parsed is not None
    assert parsed["pid"] == 99999


def test_remove_pidfile_is_idempotent(tmp_path: Path) -> None:
    """Removing a missing pidfile is a no-op (returns ``False``)."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=1234, base_dir=tmp_path,
    )
    assert server_pidfile.remove_pidfile(
        prefix="otter", port=5173, base_dir=tmp_path,
    ) is True
    # Second remove is a no-op.
    assert server_pidfile.remove_pidfile(
        prefix="otter", port=5173, base_dir=tmp_path,
    ) is False


def test_list_pidfiles_filters_by_prefix(tmp_path: Path) -> None:
    """``list_pidfiles(prefix="otter")`` skips ferret entries."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=1, base_dir=tmp_path,
    )
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5174, pid=2, base_dir=tmp_path,
    )
    server_pidfile.write_pidfile(
        prefix="ferret", host="127.0.0.1", port=5180, pid=3, base_dir=tmp_path,
    )
    otter_records = server_pidfile.list_pidfiles(
        prefix="otter", base_dir=tmp_path,
    )
    assert [r["port"] for r in otter_records] == [5173, 5174]
    ferret_records = server_pidfile.list_pidfiles(
        prefix="ferret", base_dir=tmp_path,
    )
    assert [r["port"] for r in ferret_records] == [5180]
    all_records = server_pidfile.list_pidfiles(base_dir=tmp_path)
    assert len(all_records) == 3


def test_list_pidfiles_skips_malformed_files(tmp_path: Path) -> None:
    """Half-written / malformed pidfiles are silently ignored."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=1, base_dir=tmp_path,
    )
    (tmp_path / "otter-bogus.pid").write_text("not json")
    records = server_pidfile.list_pidfiles(prefix="otter", base_dir=tmp_path)
    # Only the valid record survives.
    assert [r["port"] for r in records] == [5173]


def test_list_pidfiles_missing_dir_returns_empty(tmp_path: Path) -> None:
    """A missing pidfile dir is treated as "no servers" — not an error."""
    records = server_pidfile.list_pidfiles(
        prefix="otter", base_dir=tmp_path / "does-not-exist",
    )
    assert records == []


# ---------------------------------------------------------------------------
# stop_server / stop_all
# ---------------------------------------------------------------------------


def test_stop_server_sigterm_only_when_process_exits_quickly(
    tmp_path: Path,
) -> None:
    """SIGTERM-only path: process exits before the SIGKILL escalation."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=12345,
        base_dir=tmp_path,
    )
    # Process stays alive for one ``kill(pid, 0)`` probe (the initial
    # alive check); the post-SIGTERM poll then reports dead.
    fake_kill = _FakeKill(alive_after={12345: 1})
    fake_sleep = _FakeSleep()

    payload = {"pid": 12345, "port": 5173, "prefix": "otter"}
    result = server_pidfile.stop_server(
        payload,
        timeout=5.0,
        poll_interval=0.01,
        base_dir=tmp_path,
        kill=fake_kill,
        sleep=fake_sleep,
    )
    assert result["stopped"] is True
    assert result["signaled"] == "sigterm"
    assert result["pidfile_removed"] is True
    # Only SIGTERM (15) and the alive probe (0) should have been delivered.
    sent_signals = [sig for _, sig in fake_kill.calls]
    assert signal.SIGTERM in sent_signals
    assert signal.SIGKILL not in sent_signals
    # Pidfile is gone.
    assert not (tmp_path / "otter-5173.pid").exists()


def test_stop_server_escalates_to_sigkill_after_timeout(tmp_path: Path) -> None:
    """When SIGTERM doesn't take, SIGKILL fires only after the wait window."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=22222,
        base_dir=tmp_path,
    )
    # Process stays alive for many alive-probes — beyond the timeout we set.
    # After SIGKILL it dies on the next probe.
    fake_kill = _FakeKill(alive_after={22222: 100})
    fake_sleep = _FakeSleep()

    # ``timeout=0.0`` makes the wait loop return immediately so the test
    # doesn't burn wall-clock seconds. The SIGTERM-then-SIGKILL ordering
    # is preserved regardless of the timeout magnitude.
    payload = {"pid": 22222, "port": 5173, "prefix": "otter"}
    result = server_pidfile.stop_server(
        payload,
        timeout=0.0,
        poll_interval=0.01,
        base_dir=tmp_path,
        kill=fake_kill,
        sleep=fake_sleep,
    )
    sent_signals = [(pid, sig) for pid, sig in fake_kill.calls if sig != 0]
    # SIGTERM must come strictly before SIGKILL — never the reverse.
    assert sent_signals[0] == (22222, signal.SIGTERM)
    assert (22222, signal.SIGKILL) in sent_signals
    sigterm_idx = sent_signals.index((22222, signal.SIGTERM))
    sigkill_idx = sent_signals.index((22222, signal.SIGKILL))
    assert sigterm_idx < sigkill_idx, "SIGKILL must follow SIGTERM, never precede"
    assert result["signaled"] == "sigterm+sigkill"


def test_stop_server_already_dead_pid_cleans_pidfile(tmp_path: Path) -> None:
    """A stale pidfile (process already gone) still gets cleaned up."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=33333,
        base_dir=tmp_path,
    )
    # Process is dead from the first probe.
    fake_kill = _FakeKill(alive_after={33333: 0})
    fake_sleep = _FakeSleep()

    payload = {"pid": 33333, "port": 5173, "prefix": "otter"}
    result = server_pidfile.stop_server(
        payload,
        timeout=5.0,
        base_dir=tmp_path,
        kill=fake_kill,
        sleep=fake_sleep,
    )
    assert result["stopped"] is True
    assert result["signaled"] == "none"
    assert result["pidfile_removed"] is True
    # No real signal was delivered — only the alive probe.
    real_signals = [sig for _, sig in fake_kill.calls if sig != 0]
    assert real_signals == []


def test_stop_server_invalid_pid_reports_error(tmp_path: Path) -> None:
    """A pid <= 0 is rejected with an ``invalid_pid`` error tag."""
    payload = {"pid": 0, "port": 5173, "prefix": "otter"}
    result = server_pidfile.stop_server(
        payload, base_dir=tmp_path, kill=_FakeKill(), sleep=_FakeSleep(),
    )
    assert result["stopped"] is False
    assert result["error"] == "invalid_pid"


def test_stop_all_filters_by_port(tmp_path: Path) -> None:
    """``stop_all(prefix=..., port=N)`` only targets the matching record."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=1001,
        base_dir=tmp_path,
    )
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5174, pid=1002,
        base_dir=tmp_path,
    )
    fake_kill = _FakeKill(alive_after={1001: 1, 1002: 1})
    fake_sleep = _FakeSleep()
    results = server_pidfile.stop_all(
        prefix="otter",
        port=5173,
        timeout=1.0,
        poll_interval=0.01,
        base_dir=tmp_path,
        kill=fake_kill,
        sleep=fake_sleep,
    )
    assert len(results) == 1
    assert results[0]["pid"] == 1001
    # 5174 was untouched: pidfile still present.
    assert (tmp_path / "otter-5174.pid").exists()
    # 5173 cleaned up.
    assert not (tmp_path / "otter-5173.pid").exists()


def test_stop_all_targets_every_record(tmp_path: Path) -> None:
    """``port=None`` (the ``--all`` shape) hits every prefix-matched pid."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=2001,
        base_dir=tmp_path,
    )
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5174, pid=2002,
        base_dir=tmp_path,
    )
    server_pidfile.write_pidfile(
        prefix="ferret", host="127.0.0.1", port=5180, pid=2003,
        base_dir=tmp_path,
    )
    fake_kill = _FakeKill(alive_after={2001: 1, 2002: 1, 2003: 1})
    fake_sleep = _FakeSleep()
    results = server_pidfile.stop_all(
        prefix="otter",
        port=None,
        timeout=1.0,
        poll_interval=0.01,
        base_dir=tmp_path,
        kill=fake_kill,
        sleep=fake_sleep,
    )
    pids_stopped = sorted(r["pid"] for r in results)
    assert pids_stopped == [2001, 2002]
    # Ferret record untouched.
    assert (tmp_path / "ferret-5180.pid").exists()


# ---------------------------------------------------------------------------
# CLI dispatcher (status/stop)
# ---------------------------------------------------------------------------


def _make_args(**kwargs: Any) -> argparse.Namespace:
    """Build the minimal Namespace the management dispatcher reads."""
    defaults: dict[str, Any] = {
        "sub_action": None,
        "port": None,
        "serve_stop_all": False,
        "serve_stop_timeout": 10.0,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_status_lists_every_pidfile(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``serve status`` prints one line per matching pidfile."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=os.getpid(),
        auth_token="t1", scheme="http", base_dir=tmp_path,
    )
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5174, pid=os.getpid(),
        scheme="https", base_dir=tmp_path,
    )
    monkeypatch.setattr(
        server_pidfile, "default_pidfile_dir", lambda: tmp_path,
    )
    args = _make_args(sub_action="status")
    rc = otter_cli._dispatch_serve_management(args, action="status", prefix="otter")
    captured = capsys.readouterr()
    assert rc == 0
    assert "otter port=5173" in captured.out
    assert "otter port=5174" in captured.out
    # Auth-on row notes auth=yes; auth-off row notes auth=no.
    line_5173 = [l for l in captured.out.splitlines() if "port=5173" in l][0]
    line_5174 = [l for l in captured.out.splitlines() if "port=5174" in l][0]
    assert "auth=yes" in line_5173
    assert "auth=no" in line_5174
    assert "scheme=https" in line_5174


def test_status_empty_dir_prints_friendly_message(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pidfiles → friendly message, exit 0."""
    monkeypatch.setattr(
        server_pidfile, "default_pidfile_dir", lambda: tmp_path,
    )
    args = _make_args(sub_action="status")
    rc = otter_cli._dispatch_serve_management(args, action="status", prefix="otter")
    captured = capsys.readouterr()
    assert rc == 0
    assert "No backgrounded otter servers found" in captured.out


def test_stop_auto_targets_lone_server(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``serve stop`` with no flags hits the only matching pidfile."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=44444,
        base_dir=tmp_path,
    )
    monkeypatch.setattr(
        server_pidfile, "default_pidfile_dir", lambda: tmp_path,
    )
    # ``alive_after`` count must cover: list_pidfiles probe (CLI level
    # disambiguation), stop_all → list_pidfiles probe, stop_server alive
    # check, and any post-SIGTERM polls before the process should appear
    # dead. We give a generous budget — the fake kill recorder makes the
    # actual count irrelevant since we assert on signal *delivery*, not
    # probe count.
    fake_kill = _FakeKill(alive_after={44444: 5})
    fake_sleep = _FakeSleep()
    monkeypatch.setattr(os, "kill", fake_kill)
    # Force the wait loop to use our fake sleep so the test stays fast.
    monkeypatch.setattr(server_pidfile.time, "sleep", fake_sleep)

    args = _make_args(sub_action="stop")
    rc = otter_cli._dispatch_serve_management(args, action="stop", prefix="otter")
    out = capsys.readouterr().out
    assert rc == 0
    assert "stopped (SIGTERM)" in out
    assert "port=5173" in out
    # Pidfile cleaned up.
    assert not (tmp_path / "otter-5173.pid").exists()


def test_stop_errors_when_multiple_servers_and_no_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``serve stop`` with no flags + multiple servers → exit 2."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=11,
        base_dir=tmp_path,
    )
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5174, pid=12,
        base_dir=tmp_path,
    )
    monkeypatch.setattr(
        server_pidfile, "default_pidfile_dir", lambda: tmp_path,
    )
    args = _make_args(sub_action="stop")
    rc = otter_cli._dispatch_serve_management(args, action="stop", prefix="otter")
    err = capsys.readouterr().err
    assert rc == 2
    assert "multiple otter servers running" in err


def test_stop_all_graceful(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``serve stop --all`` graceful-stops every matching server."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=55001,
        base_dir=tmp_path,
    )
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5174, pid=55002,
        base_dir=tmp_path,
    )
    monkeypatch.setattr(
        server_pidfile, "default_pidfile_dir", lambda: tmp_path,
    )
    fake_kill = _FakeKill(alive_after={55001: 5, 55002: 5})
    fake_sleep = _FakeSleep()
    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(server_pidfile.time, "sleep", fake_sleep)

    args = _make_args(sub_action="stop", serve_stop_all=True)
    rc = otter_cli._dispatch_serve_management(args, action="stop", prefix="otter")
    out = capsys.readouterr().out
    assert rc == 0
    assert "port=5173" in out
    assert "port=5174" in out
    # Both pidfiles cleaned up.
    assert not (tmp_path / "otter-5173.pid").exists()
    assert not (tmp_path / "otter-5174.pid").exists()
    # SIGKILL was never the *first* signal sent to either pid.
    real_calls = [(pid, sig) for pid, sig in fake_kill.calls if sig != 0]
    first_signals: dict[int, int] = {}
    for pid, sig in real_calls:
        if pid not in first_signals:
            first_signals[pid] = sig
    for pid, sig in first_signals.items():
        assert sig == signal.SIGTERM, (
            f"first signal to pid {pid} was {sig}, expected SIGTERM (graceful)"
        )


def test_stop_specific_port(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``serve stop --port N`` only stops the matching server."""
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5173, pid=66001,
        base_dir=tmp_path,
    )
    server_pidfile.write_pidfile(
        prefix="otter", host="127.0.0.1", port=5174, pid=66002,
        base_dir=tmp_path,
    )
    monkeypatch.setattr(
        server_pidfile, "default_pidfile_dir", lambda: tmp_path,
    )
    fake_kill = _FakeKill(alive_after={66001: 5, 66002: 999})
    fake_sleep = _FakeSleep()
    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(server_pidfile.time, "sleep", fake_sleep)

    args = _make_args(sub_action="stop", port=5173)
    rc = otter_cli._dispatch_serve_management(args, action="stop", prefix="otter")
    assert rc == 0
    # 5174 untouched.
    assert (tmp_path / "otter-5174.pid").exists()
    # 5173 cleaned up.
    assert not (tmp_path / "otter-5173.pid").exists()


def test_management_dispatcher_works_for_ferret_prefix(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same dispatcher serves ferret pidfiles when ``prefix='ferret'``.

    This is the parity proof: the ferret CLI re-uses
    :func:`otter_cli._dispatch_serve_management` so behaviour matches the
    otter side modulo the on-disk filename prefix.
    """
    server_pidfile.write_pidfile(
        prefix="ferret", host="127.0.0.1", port=5180, pid=os.getpid(),
        base_dir=tmp_path,
    )
    monkeypatch.setattr(
        server_pidfile, "default_pidfile_dir", lambda: tmp_path,
    )
    args = _make_args(sub_action="status")
    rc = otter_cli._dispatch_serve_management(args, action="status", prefix="ferret")
    assert rc == 0
    out = capsys.readouterr().out
    assert "ferret port=5180" in out

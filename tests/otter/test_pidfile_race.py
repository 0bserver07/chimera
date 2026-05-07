"""Race-safety tests for :mod:`chimera.otter.server_pidfile`.

Wave-9 (C4) wrote a happy-path pidfile module: every test fed it a
unique port so two concurrent ``chimera otter serve --port 5173``
invocations were never exercised. The W11 task B11 found the race —
``write_pidfile`` opened the file in ``'w'`` mode and unconditionally
truncated whatever was there, so the second invocation silently
clobbered the first.

These tests pin the new contract:

* Two writes against the same path: the second raises
  :class:`PidfileLocked` (and carries enough metadata to render the
  ``"already running on port X, PID Y"`` message the CLI surfaces).
* When the recorded PID is *dead*, the second write is allowed to
  take over — that's the recovery path for a server that crashed
  without removing its pidfile.
* :func:`remove_pidfile` releases the lock and unlinks the file, so a
  subsequent write succeeds again.
* The lock is filesystem-level (not just in-process): a sibling
  Python process trying to grab the same path sees it locked.
"""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from chimera.otter import server_pidfile
from chimera.otter.server_pidfile import (
    PidfileLocked,
    remove_pidfile,
    write_pidfile,
)


# ---------------------------------------------------------------------------
# Same-process contention
# ---------------------------------------------------------------------------


def test_two_writes_second_fails(tmp_path: Path) -> None:
    """Second :func:`write_pidfile` for the same path raises PidfileLocked.

    The first call returns a path and registers the locked fd. The
    second call (against the same prefix+port) sees the lock held and
    raises with both the PID and port surfaced on the exception so the
    CLI can render ``already running on port 5173, PID NNN``.
    """
    own_pid = os.getpid()
    path1 = write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=5173,
        pid=own_pid,
        base_dir=tmp_path,
    )
    assert path1.exists()
    try:
        with pytest.raises(PidfileLocked) as excinfo:
            write_pidfile(
                prefix="otter",
                host="127.0.0.1",
                port=5173,
                pid=own_pid + 1,
                base_dir=tmp_path,
            )
        # Error message includes both port and the live PID so the
        # CLI can print "already running on port 5173, PID 12345".
        assert "5173" in str(excinfo.value)
        assert str(own_pid) in str(excinfo.value)
        assert excinfo.value.pid == own_pid
        assert excinfo.value.port == 5173
        # Original payload still intact — the second write must not
        # truncate it.
        first = server_pidfile.read_pidfile(path1)
        assert first is not None
        assert first["pid"] == own_pid
    finally:
        remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path)


def test_dead_pid_taken_over(tmp_path: Path) -> None:
    """A pidfile owned by a dead PID is recovered, not refused.

    The previous process crashed without :func:`remove_pidfile` running,
    so the on-disk PID names a process that no longer exists. The next
    ``write_pidfile`` call against the same port should succeed (taking
    over) rather than refusing forever.
    """
    own_pid = os.getpid()
    # Seed a pidfile with a fake PID and then release the lock so the
    # situation matches "previous process crashed; lock released by
    # kernel on exit; on-disk PID is dead".
    write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=5173,
        pid=999_999_999,  # absurdly high — guaranteed dead
        base_dir=tmp_path,
    )
    # Release the lock without touching the file contents — emulates
    # the "process exited; OS released the flock" path.
    abs_path = str(
        server_pidfile.pidfile_path(
            "otter", 5173, base_dir=tmp_path,
        ).resolve(strict=False)
    )
    fd = server_pidfile._LOCKED_FDS.pop(abs_path)  # type: ignore[attr-defined]
    server_pidfile._release_lock(fd)  # type: ignore[attr-defined]
    os.close(fd)
    # Second write should succeed — the recorded PID is dead.
    path = write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=5173,
        pid=own_pid,
        base_dir=tmp_path,
    )
    payload = server_pidfile.read_pidfile(path)
    assert payload is not None
    assert payload["pid"] == own_pid  # taken over
    remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path)


def test_release_unlinks_file(tmp_path: Path) -> None:
    """:func:`remove_pidfile` releases the lock and unlinks the file."""
    write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=5173,
        pid=os.getpid(),
        base_dir=tmp_path,
    )
    pidfile = tmp_path / "otter-5173.pid"
    assert pidfile.exists()
    assert remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path) is True
    assert not pidfile.exists()
    # And the lock is released — a fresh write succeeds.
    write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=5173,
        pid=os.getpid(),
        base_dir=tmp_path,
    )
    assert pidfile.exists()
    remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path)


def test_remove_pidfile_idempotent_after_release(tmp_path: Path) -> None:
    """Calling :func:`remove_pidfile` twice is a no-op the second time."""
    write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=5173,
        pid=os.getpid(),
        base_dir=tmp_path,
    )
    assert remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path) is True
    assert remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path) is False


# ---------------------------------------------------------------------------
# Cross-process contention
# ---------------------------------------------------------------------------


def _child_grab_lock(
    base_dir: str, port: int, ready_path: str, hold_seconds: float,
) -> None:
    """Subprocess entry: claim the lock, signal readiness, hold briefly.

    Used by :func:`test_cross_process_lock_blocks` to prove the flock
    is enforced at the filesystem layer (a separate process sees the
    lock contended) — not just within our in-process fd registry.
    """
    import time as _time

    write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=port,
        pid=os.getpid(),
        base_dir=Path(base_dir),
    )
    # Touch the ready-file so the parent knows we hold the lock.
    Path(ready_path).write_text(str(os.getpid()))
    _time.sleep(hold_seconds)
    # Release on graceful exit so the parent can take over.
    remove_pidfile(prefix="otter", port=port, base_dir=Path(base_dir))


def test_cross_process_lock_blocks(tmp_path: Path) -> None:
    """A sibling Python process holding the lock makes our call fail.

    The flock is OS-level: a child process owning the fd is enough for
    our parent process to see ``EAGAIN`` on its own attempt, with no
    shared in-process state. This is the test that proves the fix
    isn't just a Python-level dict-of-fds.
    """
    ready = tmp_path / "ready"
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(
        target=_child_grab_lock,
        args=(str(tmp_path), 5173, str(ready), 3.0),
    )
    proc.start()
    try:
        # Wait for the child to claim the lock (bounded by 5s — a CI
        # sandbox cold-start budget).
        deadline = 5.0
        step = 0.05
        waited = 0.0
        import time as _time
        while not ready.exists() and waited < deadline:
            _time.sleep(step)
            waited += step
        assert ready.exists(), "child never reported lock acquisition"
        # Now the parent's attempt must see contention.
        with pytest.raises(PidfileLocked) as excinfo:
            write_pidfile(
                prefix="otter",
                host="127.0.0.1",
                port=5173,
                pid=os.getpid(),
                base_dir=tmp_path,
            )
        assert excinfo.value.port == 5173
        assert excinfo.value.pid == proc.pid
    finally:
        proc.join(timeout=10)
        if proc.is_alive():  # pragma: no cover - defensive
            proc.terminate()
            proc.join(timeout=2)
        # Defensive cleanup so a follow-on test never inherits a
        # stale lock on this path.
        try:
            remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Server callsite resilience
# ---------------------------------------------------------------------------


def test_server_pidfile_swallows_locked(tmp_path: Path) -> None:
    """Inner contract: :func:`write_pidfile` raises :class:`PidfileLocked`.

    Originally (waves 9–11) the server's wrapper caught every exception
    so a contended pidfile never tanked the serving path. Wave 13
    (W13-E3) replaced that bare ``except`` with a
    :class:`PidfileLocked`-specific catch that prints a stderr
    breadcrumb and exits ``rc=2`` — see
    ``tests/otter/test_pidfile_error_surface.py`` for the new
    server-level behaviour. This test now only pins the *inner* invariant
    that ``write_pidfile`` raises :class:`PidfileLocked` (and that the
    exception is catchable as ``Exception``), which the server callsite
    relies on.
    """
    write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=5173,
        pid=os.getpid(),
        base_dir=tmp_path,
    )
    captured: list[BaseException] = []
    try:
        write_pidfile(
            prefix="otter",
            host="127.0.0.1",
            port=5173,
            pid=os.getpid() + 1,
            base_dir=tmp_path,
        )
    except Exception as exc:  # noqa: BLE001 - mimics server.py envelope
        captured.append(exc)
    assert len(captured) == 1
    assert isinstance(captured[0], PidfileLocked)
    remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path)

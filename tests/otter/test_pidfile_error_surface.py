"""Pidfile error surfacing — wave 13, task W13-E3.

The wave-11 fix (B11) made :func:`chimera.otter.server_pidfile.write_pidfile`
race-safe: a second ``chimera otter serve`` against an already-bound port
raises :class:`PidfileLocked` instead of silently truncating the first
process's pidfile. The server callsite in
:meth:`chimera.otter.server.OtterServer._maybe_write_pidfile` still wrapped
that in ``except Exception``, so the user got no message — just a quiet
swallow followed by an ``EADDRINUSE`` later.

This module pins the new contract:

* Pidfile contention surfaces a user-facing
  ``"ERROR: chimera otter serve already running (pid=X) on port=Y. ..."``
  on ``stderr``.
* The server exits with ``rc=2`` (via ``SystemExit(2)``) instead of
  silently continuing with a half-started listener.
* Non-``PidfileLocked`` exceptions still propagate untouched (no new
  bare ``except``).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from chimera.otter import server_pidfile
from chimera.otter.server import OtterServer
from chimera.otter.server_pidfile import remove_pidfile, write_pidfile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path: Path) -> OtterServer:
    """An :class:`OtterServer` that writes its pidfile under ``tmp_path``.

    No socket is bound — we drive ``_maybe_write_pidfile`` directly to
    isolate the surfacing behaviour from the rest of ``start``.
    """
    return OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=5173,
        pidfile_prefix="otter",
        pidfile_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# Surfacing on lock contention
# ---------------------------------------------------------------------------


def test_pidfile_locked_prints_error_and_exits_rc2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    server: OtterServer,
) -> None:
    """A contended pidfile surfaces stderr + ``SystemExit(2)``."""
    # Seed an existing pidfile owned by this process so the lock is held
    # when the server-under-test tries to take it.
    own_pid = os.getpid()
    write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=5173,
        pid=own_pid,
        base_dir=tmp_path,
    )
    try:
        with pytest.raises(SystemExit) as excinfo:
            server._maybe_write_pidfile()
        assert excinfo.value.code == 2

        captured = capsys.readouterr()
        # Error goes to stderr, not stdout.
        assert captured.out == ""
        # The message includes the literal directives the task spec
        # called out so users can find both the running PID and the
        # port they need to free / change.
        assert "ERROR: chimera otter serve already running" in captured.err
        assert f"pid={own_pid}" in captured.err
        assert "port=5173" in captured.err
        assert "Stop it first or use a different --port." in captured.err
    finally:
        remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path)


def test_pidfile_locked_does_not_swallow_silently(
    tmp_path: Path,
    server: OtterServer,
) -> None:
    """Regression: pre-V13 the bare ``except`` ate the lock event."""
    write_pidfile(
        prefix="otter",
        host="127.0.0.1",
        port=5173,
        pid=os.getpid(),
        base_dir=tmp_path,
    )
    try:
        # The fix guarantees we exit; if the bare except came back, this
        # call would return ``None`` quietly and the server would carry
        # on with ``self._pidfile_path = None`` — exactly the
        # pre-fix behaviour we're guarding against.
        with pytest.raises(SystemExit):
            server._maybe_write_pidfile()
        assert server._pidfile_path is None
    finally:
        remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path)


# ---------------------------------------------------------------------------
# Other exceptions still bubble (no new bare except)
# ---------------------------------------------------------------------------


def test_non_locked_exceptions_propagate(
    monkeypatch: pytest.MonkeyPatch,
    server: OtterServer,
) -> None:
    """A non-PidfileLocked failure (e.g. PermissionError) propagates.

    The pre-V13 ``except Exception`` masked these too; the new fix only
    catches :class:`PidfileLocked`.
    """
    def boom(**kwargs: object) -> Path:  # noqa: ARG001
        raise PermissionError("refused")

    monkeypatch.setattr(server_pidfile, "write_pidfile", boom)
    with pytest.raises(PermissionError, match="refused"):
        server._maybe_write_pidfile()


def test_no_pidfile_prefix_is_noop(tmp_path: Path) -> None:
    """``pidfile_prefix=None`` short-circuits before any write_pidfile call."""
    srv = OtterServer(
        agent_factory=None,
        host="127.0.0.1",
        port=5173,
        pidfile_prefix=None,
        pidfile_dir=tmp_path,
    )
    # Must not raise, must not write anything.
    srv._maybe_write_pidfile()
    assert srv._pidfile_path is None


# ---------------------------------------------------------------------------
# Successful path is unchanged
# ---------------------------------------------------------------------------


def test_pidfile_write_success_still_records_path(
    tmp_path: Path, server: OtterServer,
) -> None:
    """Happy path: the new wrapper still records ``self._pidfile_path``."""
    server._maybe_write_pidfile()
    assert server._pidfile_path is not None
    assert server._pidfile_path.exists()
    remove_pidfile(prefix="otter", port=5173, base_dir=tmp_path)

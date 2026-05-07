"""PTY (pseudo-terminal) primitives for ``chimera otter serve``.

Wraps the stdlib :mod:`pty` + :mod:`os` primitives so the otter HTTP
server can host long-running interactive subprocesses (``vim``,
``python -i``, ``npm run dev``, …) and stream both stdin and stdout to
remote clients. Builds on the same model as :mod:`chimera.env.shell`'s
``PersistentShell`` but exposes the kernel-level master fd directly so
TTY-aware programs (curses, full-screen editors, REPLs that want raw
input) work end-to-end.

Trademark hygiene: this module never names the upstream open-source
coding agent in user-visible source.

API
---

The HTTP layer in :mod:`chimera.otter.server` exposes:

================================================== ====== =========================
Path                                                Method Purpose
================================================== ====== =========================
``/session/<id>/pty/start``                          POST   Spawn a PTY-backed
                                                            subprocess. Body:
                                                            ``{"command": "...",``
                                                            ``"cols": 80, "rows": 24,``
                                                            ``"env": {...}}``. Returns
                                                            ``{"pty_id": "..."}``.
``/session/<id>/pty/<pty_id>/input``                 POST   Append stdin to a
                                                            running PTY. Body:
                                                            ``{"data": "..."}``.
``/session/<id>/pty/<pty_id>/output``                GET    Read pending stdout.
                                                            Returns ``{"data":``
                                                            ``"...", "exit": null}``.
``/session/<id>/pty/<pty_id>/resize``                POST   Resize the PTY window.
                                                            Body: ``{"cols": 120,``
                                                            ``"rows": 40}``.
``/session/<id>/pty/<pty_id>/stop``                  POST   Send SIGTERM; returns
                                                            ``{"exit_code": ...}``.
``/session/<id>/pty/stream``                         GET    SSE: events
                                                            ``{"data": "..."}``
                                                            for every read chunk
                                                            until EOF / stop.
================================================== ====== =========================

This module exposes the :class:`PtyManager` class so the server-side
handlers in :mod:`chimera.otter.server` keep the protocol logic and
defer process / fd / signal handling to here.

Implementation notes
--------------------

* **Stdlib only.** Uses ``pty.openpty`` + ``os.fork`` is too risky in
  a server context (forking a process that has many threads is
  unsupported on macOS). Instead we shell out via
  :class:`subprocess.Popen` with ``stdin``, ``stdout``, ``stderr`` all
  hooked up to the slave fd and the master fd retained in-process for
  read / write. The reader thread forwards chunks into a thread-safe
  ``deque`` buffer (drained by ``output``) and onto every SSE
  subscriber queue.
* **macOS / Linux only.** The :mod:`pty` module is POSIX-only; on
  Windows the manager raises :class:`RuntimeError` at ``start`` time so
  the server returns 500 with a structured error rather than crashing.
* **No tmux dependency.** Unlike :class:`chimera.env.shell.PersistentShell`
  the PTY here is process-private: when the server stops the subprocess
  is reaped and the master fd is closed.
"""
from __future__ import annotations

import errno
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "PtySession",
    "PtyManager",
    "is_supported",
]


def is_supported() -> bool:
    """Return ``True`` on POSIX systems where ``pty.openpty`` works.

    PTY support is a hard "yes / no" line: macOS, Linux, BSDs work;
    Windows does not. ``sys.platform`` is the cheapest probe.
    """
    return sys.platform != "win32"


@dataclass
class PtySession:
    """One running PTY-backed subprocess.

    Attributes:
        pty_id: Random url-safe id (server-generated).
        process: The :class:`subprocess.Popen` handle.
        master_fd: Master side of the PTY (parent reads/writes here).
        slave_fd: Slave side (handed to the child; closed in parent
            after spawn, or kept open for resize ioctls).
        cols: Last-known terminal width.
        rows: Last-known terminal height.
        buffer: Output buffer; cleared by every ``output`` poll.
        subscribers: Per-subscriber chunk queues for SSE streaming.
        lock: Guards ``buffer`` / ``subscribers`` / ``exit_code``.
        reader_thread: Background reader; drains ``master_fd`` into
            ``buffer`` and ``subscribers``.
        exit_code: Set when the child exits; ``None`` while running.
        created_at: Wall-clock timestamp of spawn.
    """

    pty_id: str
    process: subprocess.Popen[bytes]
    master_fd: int
    slave_fd: int
    cols: int
    rows: int
    buffer: bytearray = field(default_factory=bytearray)
    subscribers: list[Any] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    reader_thread: threading.Thread | None = None
    exit_code: int | None = None
    created_at: float = field(default_factory=time.time)


class PtyManager:
    """In-memory registry of :class:`PtySession` instances.

    One :class:`PtyManager` is owned by the otter server; sessions are
    keyed by ``pty_id`` and routed to per-session methods by the HTTP
    layer.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}
        self._lock = threading.Lock()

    # ------ Lifecycle ----------------------------------------------------

    def start(
        self,
        command: str | list[str],
        *,
        cols: int = 80,
        rows: int = 24,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> PtySession:
        """Spawn a subprocess attached to a fresh PTY.

        Args:
            command: Command line. Strings are passed to ``/bin/sh``;
                lists are spawned directly with ``shell=False``.
            cols: Initial terminal width (TIOCSWINSZ).
            rows: Initial terminal height.
            env: Optional env override; merged with ``os.environ``.
            cwd: Optional working directory for the child.

        Returns:
            The new :class:`PtySession`.

        Raises:
            RuntimeError: When PTYs aren't supported on this platform.
            ValueError: When ``command`` is empty.
        """
        if not is_supported():
            raise RuntimeError(
                "PTY routes are not supported on this platform "
                f"({sys.platform})"
            )
        if not command:
            raise ValueError("command must not be empty")

        import pty

        master_fd, slave_fd = pty.openpty()
        # Apply initial winsize via TIOCSWINSZ before the child sees it.
        self._set_winsize(master_fd, cols, rows)

        merged_env = dict(os.environ)
        if env:
            merged_env.update({str(k): str(v) for k, v in env.items()})
        # Force a sane TERM so curses-style apps render.
        merged_env.setdefault("TERM", "xterm-256color")

        if isinstance(command, str):
            argv: list[str] = ["/bin/sh", "-c", command]
        else:
            argv = list(command)

        # ``start_new_session=True`` so ctrl+C from the parent doesn't
        # propagate to the child; we always send SIGTERM via .stop().
        process = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=merged_env,
            cwd=cwd,
            start_new_session=True,
            bufsize=0,
        )

        # WHY: close the slave end in the parent so when the child
        # exits, the master fd reaches EIO/EOF instead of blocking
        # forever in ``os.read``. Tracked separately as ``slave_fd``
        # was already used by ``Popen``; the child has its own copy.
        try:
            os.close(slave_fd)
        except OSError:
            pass
        slave_fd = -1

        pty_id = secrets.token_urlsafe(8)
        session = PtySession(
            pty_id=pty_id,
            process=process,
            master_fd=master_fd,
            slave_fd=slave_fd,
            cols=cols,
            rows=rows,
        )
        thread = threading.Thread(
            target=self._reader_loop,
            args=(session,),
            name=f"pty-reader-{pty_id}",
            daemon=True,
        )
        session.reader_thread = thread
        with self._lock:
            self._sessions[pty_id] = session
        thread.start()
        return session

    def get(self, pty_id: str) -> PtySession | None:
        with self._lock:
            return self._sessions.get(pty_id)

    def list_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())

    def write(self, pty_id: str, data: str | bytes) -> int:
        """Append ``data`` to the PTY's stdin. Returns bytes written."""
        session = self.get(pty_id)
        if session is None:
            raise KeyError(f"no such pty: {pty_id}")
        if isinstance(data, str):
            payload = data.encode("utf-8")
        else:
            payload = bytes(data)
        try:
            return os.write(session.master_fd, payload)
        except OSError as exc:
            if exc.errno in (errno.EBADF, errno.EIO):
                return 0
            raise

    def read(self, pty_id: str) -> tuple[bytes, int | None]:
        """Drain the buffered stdout. Returns ``(data, exit_code)``."""
        session = self.get(pty_id)
        if session is None:
            raise KeyError(f"no such pty: {pty_id}")
        with session.lock:
            data = bytes(session.buffer)
            session.buffer.clear()
            return data, session.exit_code

    def resize(self, pty_id: str, cols: int, rows: int) -> None:
        session = self.get(pty_id)
        if session is None:
            raise KeyError(f"no such pty: {pty_id}")
        with session.lock:
            session.cols = max(1, int(cols))
            session.rows = max(1, int(rows))
            self._set_winsize(session.master_fd, session.cols, session.rows)

    def stop(self, pty_id: str, *, timeout: float = 2.0) -> int | None:
        """Terminate a PTY session; returns the exit code (or ``None``)."""
        session = self.get(pty_id)
        if session is None:
            return None
        proc = session.process
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.send_signal(signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
        with session.lock:
            session.exit_code = proc.returncode
        # Wake any subscribers so SSE can drain.
        self._broadcast(session, b"")
        self._cleanup_fds(session)
        with self._lock:
            self._sessions.pop(pty_id, None)
        return session.exit_code

    def shutdown_all(self) -> None:
        for pid in self.list_ids():
            try:
                self.stop(pid, timeout=0.5)
            except Exception:  # noqa: BLE001
                pass

    # ------ Subscribers --------------------------------------------------

    def subscribe(self, pty_id: str) -> Any:
        """Return a per-subscriber queue that receives every read chunk.

        The queue is a :class:`queue.Queue` of ``bytes``; ``b""`` is
        pushed when the child exits.
        """
        from queue import Queue

        session = self.get(pty_id)
        if session is None:
            raise KeyError(f"no such pty: {pty_id}")
        q: Any = Queue()
        with session.lock:
            session.subscribers.append(q)
            # Push any already-buffered data so a late subscriber sees
            # the early output too.
            if session.buffer:
                q.put(bytes(session.buffer))
            if session.exit_code is not None:
                q.put(b"")
        return q

    def unsubscribe(self, pty_id: str, q: Any) -> None:
        session = self.get(pty_id)
        if session is None:
            return
        with session.lock:
            try:
                session.subscribers.remove(q)
            except ValueError:
                pass

    # ------ Internals ----------------------------------------------------

    def _reader_loop(self, session: PtySession) -> None:
        fd = session.master_fd
        try:
            while True:
                try:
                    chunk = os.read(fd, 4096)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        # Slave side closed; treat as EOF.
                        chunk = b""
                    elif exc.errno == errno.EBADF:
                        chunk = b""
                    else:
                        raise
                if not chunk:
                    break
                with session.lock:
                    session.buffer.extend(chunk)
                self._broadcast(session, chunk)
                # Stop quickly if the child exited mid-read.
                if session.process.poll() is not None and not chunk:
                    break
        finally:
            # Reap the child. ``Popen.wait`` releases zombies.
            try:
                code = session.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                code = session.process.returncode
            with session.lock:
                session.exit_code = code
            self._broadcast(session, b"")

    @staticmethod
    def _broadcast(session: PtySession, chunk: bytes) -> None:
        with session.lock:
            stale: list[Any] = []
            for q in session.subscribers:
                try:
                    q.put_nowait(chunk)
                except Exception:  # noqa: BLE001
                    stale.append(q)
            for q in stale:
                try:
                    session.subscribers.remove(q)
                except ValueError:
                    pass

    @staticmethod
    def _set_winsize(fd: int, cols: int, rows: int) -> None:
        try:
            import fcntl
            import struct
            import termios
        except ImportError:  # pragma: no cover - non-POSIX
            return
        try:
            fcntl.ioctl(
                fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0),
            )
        except OSError:
            # Best-effort; resize failure shouldn't take down the PTY.
            pass

    @staticmethod
    def _cleanup_fds(session: PtySession) -> None:
        # ``slave_fd`` was closed in :meth:`start` so the master would
        # see EOF; only the master fd remains open after spawn.
        for fd in (session.master_fd, session.slave_fd):
            if fd is None or fd < 0:
                continue
            try:
                os.close(fd)
            except OSError:
                pass

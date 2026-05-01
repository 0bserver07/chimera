"""Persistent PID files for ``chimera otter serve`` / ``chimera ferret serve``.

When a user backgrounds ``chimera otter serve`` (or ``chimera ferret
serve``), there is no easy way to discover the PID, port, or auth-token
state of the running process from a separate shell. This module owns the
on-disk record so the ``serve status`` and ``serve stop`` subcommands
can list and gracefully terminate those background servers without
hand-rolling ``ps`` / ``lsof`` parsing.

Layout
------

PID files live under ``~/.chimera/run/<prefix>-<port>.pid`` (one per
running server). Each file is JSON:

.. code-block:: json

    {
      "pid": 12345,
      "host": "127.0.0.1",
      "port": 5173,
      "prefix": "otter",
      "auth_token_hash": "sha256:…",
      "started_at": 1714500000.0,
      "scheme": "http"
    }

``auth_token_hash`` is ``"sha256:<hex>"`` when the server is configured
with ``--auth-token`` and ``null`` otherwise. Storing only the SHA-256
keeps the bearer secret off disk while still letting a future
``serve stop --auth-token …`` flow assert the caller knows the token.

Graceful-stop contract
----------------------

:func:`stop_server` and :func:`stop_all` follow the project-wide rule
(see ``CLAUDE.md``): SIGTERM first, wait up to ``timeout`` seconds, only
escalate to SIGKILL when the process has not exited. ``kill -9`` /
``SIGKILL`` is **never** the first signal we send.

Stdlib only — :mod:`os`, :mod:`signal`, :mod:`hashlib`, :mod:`json`,
:mod:`pathlib`, :mod:`time`. No third-party deps.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import time
from pathlib import Path
from typing import Any


__all__ = [
    "default_pidfile_dir",
    "pidfile_path",
    "hash_auth_token",
    "write_pidfile",
    "remove_pidfile",
    "read_pidfile",
    "list_pidfiles",
    "process_alive",
    "stop_server",
    "stop_all",
]


def default_pidfile_dir() -> Path:
    """Return the default directory for serve PID files.

    Defaults to ``~/.chimera/run`` honoring the live ``Path.home()`` so
    tests pinning ``$HOME`` to ``tmp_path`` keep their hermeticity.

    Returns:
        ``~/.chimera/run`` (not created).
    """
    return Path.home() / ".chimera" / "run"


def pidfile_path(prefix: str, port: int, *, base_dir: Path | None = None) -> Path:
    """Return the absolute pidfile path for *prefix* + *port*.

    Args:
        prefix: Server flavor (``"otter"`` or ``"ferret"``). Used as
            the filename prefix so concurrent otter + ferret servers on
            different ports never collide.
        port: Bound port. ``0`` is allowed (for tests with ephemeral
            ports) but the caller should write the pidfile *after*
            :meth:`OtterServer.start` resolves the real port.
        base_dir: Override directory. Defaults to
            :func:`default_pidfile_dir`. Tests pin ``tmp_path``.

    Returns:
        ``<base_dir>/<prefix>-<port>.pid``.
    """
    root = base_dir if base_dir is not None else default_pidfile_dir()
    return root / f"{prefix}-{int(port)}.pid"


def hash_auth_token(token: str | None) -> str | None:
    """Return ``"sha256:<hex>"`` for *token*, or ``None`` when unset.

    The plaintext token is never persisted — only its SHA-256 is. This
    lets a future ``serve stop --auth-token …`` flow verify the caller
    knows the secret without exposing it on disk.

    Args:
        token: The raw bearer token, or ``None`` when the server runs
            without auth.

    Returns:
        ``"sha256:<hex>"`` when *token* is non-empty, else ``None``.
    """
    if not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def write_pidfile(
    *,
    prefix: str,
    host: str,
    port: int,
    pid: int | None = None,
    auth_token: str | None = None,
    scheme: str = "http",
    started_at: float | None = None,
    base_dir: Path | None = None,
) -> Path:
    """Write the JSON pidfile for a freshly-started server.

    Args:
        prefix: ``"otter"`` or ``"ferret"``.
        host: Bind host (informational; never used for kill targeting).
        port: Resolved bind port.
        pid: Process id. Defaults to :func:`os.getpid` so the common case
            of "writing my own pidfile" is one call.
        auth_token: Raw token, hashed via :func:`hash_auth_token`.
        scheme: ``"http"`` or ``"https"``.
        started_at: UNIX seconds. Defaults to :func:`time.time`.
        base_dir: Override directory. Defaults to
            :func:`default_pidfile_dir` (created on first write).

    Returns:
        The absolute pidfile path written.
    """
    root = base_dir if base_dir is not None else default_pidfile_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = pidfile_path(prefix, port, base_dir=root)
    payload: dict[str, Any] = {
        "pid": int(pid if pid is not None else os.getpid()),
        "host": str(host),
        "port": int(port),
        "prefix": str(prefix),
        "auth_token_hash": hash_auth_token(auth_token),
        "started_at": float(started_at if started_at is not None else time.time()),
        "scheme": str(scheme),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def remove_pidfile(
    *,
    prefix: str,
    port: int,
    base_dir: Path | None = None,
) -> bool:
    """Remove the pidfile for *prefix* + *port*. Idempotent.

    Args:
        prefix: ``"otter"`` or ``"ferret"``.
        port: Bound port (the same value passed to :func:`write_pidfile`).
        base_dir: Override directory. Defaults to
            :func:`default_pidfile_dir`.

    Returns:
        ``True`` when a file was actually unlinked, ``False`` when none
        existed (still the success path; caller should not treat ``False``
        as an error).
    """
    path = pidfile_path(prefix, port, base_dir=base_dir)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def read_pidfile(path: Path) -> dict[str, Any] | None:
    """Return the parsed JSON payload at *path*, or ``None`` on failure.

    Failures (file missing, JSON malformed, payload not a dict) collapse
    to ``None`` so callers iterate over ``list_pidfiles`` without
    tripping on a half-written or stale entry.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def list_pidfiles(
    *,
    prefix: str | None = None,
    base_dir: Path | None = None,
    kill: Any = None,
) -> list[dict[str, Any]]:
    """Return parsed pidfile records under *base_dir*.

    Args:
        prefix: When set, restrict to ``<prefix>-*.pid`` (e.g. ``"otter"``).
            ``None`` returns every pidfile regardless of flavor.
        base_dir: Override directory. Defaults to
            :func:`default_pidfile_dir`. A missing directory yields ``[]``
            (no entries — not an error).

    Returns:
        List of payload dicts, each augmented with the absolute
        ``"path"`` and a boolean ``"alive"`` (per :func:`process_alive`)
        so callers don't have to re-stat each entry. Sorted by port.
    """
    root = base_dir if base_dir is not None else default_pidfile_dir()
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_file() or entry.suffix != ".pid":
            continue
        if prefix is not None and not entry.name.startswith(f"{prefix}-"):
            continue
        payload = read_pidfile(entry)
        if payload is None:
            continue
        payload["path"] = str(entry)
        try:
            payload["alive"] = process_alive(
                int(payload.get("pid", 0)), kill=kill,
            )
        except (TypeError, ValueError):
            payload["alive"] = False
        out.append(payload)
    out.sort(key=lambda p: int(p.get("port", 0)))
    return out


def process_alive(pid: int, *, kill: Any = None) -> bool:
    """Return ``True`` when *pid* names a live process owned by us.

    Uses ``kill(pid, 0)`` (defaults to :func:`os.kill`) — the standard
    POSIX way to ping a process without delivering a real signal.
    ``ProcessLookupError`` means the pid is not running.
    ``PermissionError`` means the pid is running but owned by another
    user — for our purposes (the caller is the same user who started
    the server) we treat that as "alive".

    Args:
        pid: Candidate process id. ``<= 0`` always returns ``False``.
        kill: Override :func:`os.kill`. Tests inject a recorder so the
            probe never touches a real pid.

    Returns:
        ``True`` when the process appears live, ``False`` otherwise.
    """
    if pid <= 0:
        return False
    kill_fn = kill if kill is not None else os.kill
    try:
        kill_fn(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still "alive" from
        # the user's perspective.
        return True
    except OSError:
        return False
    return True


def _wait_for_exit(
    pid: int,
    *,
    timeout: float,
    poll_interval: float = 0.1,
    sleep: Any = None,
    kill: Any = None,
) -> bool:
    """Poll ``process_alive`` until it returns ``False`` or *timeout* fires.

    Args:
        pid: Process id to watch.
        timeout: Total seconds to wait before giving up.
        poll_interval: Seconds between :func:`process_alive` checks.
        sleep: Override sleep callable (tests inject a fake to avoid
            burning real wall-clock seconds).
        kill: Override :func:`os.kill` for the alive probe.

    Returns:
        ``True`` once ``process_alive(pid)`` is ``False`` (i.e. the
        process has exited). ``False`` when *timeout* expired with the
        process still alive.
    """
    sleep_fn = sleep if sleep is not None else time.sleep
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if not process_alive(pid, kill=kill):
            return True
        if time.monotonic() >= deadline:
            return False
        sleep_fn(poll_interval)


def stop_server(
    payload: dict[str, Any],
    *,
    timeout: float = 10.0,
    poll_interval: float = 0.1,
    base_dir: Path | None = None,
    kill: Any = None,
    sleep: Any = None,
) -> dict[str, Any]:
    """Gracefully stop the server described by *payload*.

    SIGTERM first; wait up to *timeout* seconds; only then escalate to
    SIGKILL. This matches the project-wide graceful-shutdown rule (see
    ``CLAUDE.md``): never use ``kill -9`` as the first signal.

    Args:
        payload: A dict from :func:`list_pidfiles` (or
            :func:`read_pidfile`). Must carry ``pid`` and ``port``.
        timeout: Seconds to wait between SIGTERM and the SIGKILL
            escalation.
        poll_interval: Polling cadence while waiting.
        base_dir: Override directory for pidfile cleanup. Defaults to
            :func:`default_pidfile_dir`.
        kill: Override for :func:`os.kill` (tests inject a recorder).
        sleep: Override for :func:`time.sleep` (tests inject a fake).

    Returns:
        A status dict with keys:

        * ``pid`` — target pid
        * ``port`` — target port
        * ``prefix`` — flavor (``"otter"`` / ``"ferret"``)
        * ``signaled`` — ``"sigterm"`` / ``"sigterm+sigkill"`` /
          ``"none"`` (no signal sent because the process was already
          gone)
        * ``stopped`` — ``True`` when the process is no longer alive
          at function exit
        * ``pidfile_removed`` — ``True`` when the pidfile was unlinked
        * ``error`` — optional string describing a non-fatal failure
          (e.g. ``"sigkill_failed"``)
    """
    kill_fn = kill if kill is not None else os.kill
    pid = int(payload.get("pid", 0))
    port = int(payload.get("port", 0))
    prefix = str(payload.get("prefix", ""))
    result: dict[str, Any] = {
        "pid": pid,
        "port": port,
        "prefix": prefix,
        "signaled": "none",
        "stopped": False,
        "pidfile_removed": False,
    }

    if pid <= 0:
        result["error"] = "invalid_pid"
        return result

    if not process_alive(pid, kill=kill):
        # Process already gone — clean up the stale pidfile and report
        # success (idempotent stop).
        result["stopped"] = True
        if prefix and port:
            result["pidfile_removed"] = remove_pidfile(
                prefix=prefix, port=port, base_dir=base_dir,
            )
        return result

    # Step 1: SIGTERM. Graceful first, per CLAUDE.md.
    try:
        kill_fn(pid, signal.SIGTERM)
        result["signaled"] = "sigterm"
    except ProcessLookupError:
        # Race: process exited between alive-check and kill. Treat as
        # success.
        result["stopped"] = True
        if prefix and port:
            result["pidfile_removed"] = remove_pidfile(
                prefix=prefix, port=port, base_dir=base_dir,
            )
        return result
    except OSError as exc:
        result["error"] = f"sigterm_failed: {exc}"
        return result

    # Step 2: wait for graceful exit.
    if _wait_for_exit(
        pid, timeout=timeout, poll_interval=poll_interval,
        sleep=sleep, kill=kill,
    ):
        result["stopped"] = True
        if prefix and port:
            result["pidfile_removed"] = remove_pidfile(
                prefix=prefix, port=port, base_dir=base_dir,
            )
        return result

    # Step 3: SIGKILL escalation only after the timeout. We log the
    # escalation in ``signaled`` so callers can report it to the user.
    try:
        kill_fn(pid, signal.SIGKILL)
        result["signaled"] = "sigterm+sigkill"
    except ProcessLookupError:
        # Race: died during the SIGKILL window. Still success.
        result["stopped"] = True
        if prefix and port:
            result["pidfile_removed"] = remove_pidfile(
                prefix=prefix, port=port, base_dir=base_dir,
            )
        return result
    except OSError as exc:
        result["error"] = f"sigkill_failed: {exc}"
        return result

    # One last poll to confirm the kill landed.
    if _wait_for_exit(
        pid, timeout=2.0, poll_interval=poll_interval,
        sleep=sleep, kill=kill,
    ):
        result["stopped"] = True
        if prefix and port:
            result["pidfile_removed"] = remove_pidfile(
                prefix=prefix, port=port, base_dir=base_dir,
            )
    else:
        result["error"] = "still_alive_after_sigkill"
    return result


def stop_all(
    *,
    prefix: str,
    port: int | None = None,
    timeout: float = 10.0,
    poll_interval: float = 0.1,
    base_dir: Path | None = None,
    kill: Any = None,
    sleep: Any = None,
) -> list[dict[str, Any]]:
    """Stop every server matching *prefix* (and optionally *port*).

    Args:
        prefix: ``"otter"`` or ``"ferret"``.
        port: When set, only the matching pidfile is targeted. ``None``
            means "every pidfile under this prefix" (i.e. ``--all``).
        timeout: Per-server SIGTERM-to-SIGKILL window.
        poll_interval: Polling cadence while waiting.
        base_dir: Override directory.
        kill: Override for :func:`os.kill`.
        sleep: Override for :func:`time.sleep`.

    Returns:
        One :func:`stop_server` result per matching pidfile. Empty list
        when nothing matched.
    """
    payloads = list_pidfiles(prefix=prefix, base_dir=base_dir)
    if port is not None:
        payloads = [p for p in payloads if int(p.get("port", -1)) == int(port)]
    return [
        stop_server(
            p,
            timeout=timeout,
            poll_interval=poll_interval,
            base_dir=base_dir,
            kill=kill,
            sleep=sleep,
        )
        for p in payloads
    ]

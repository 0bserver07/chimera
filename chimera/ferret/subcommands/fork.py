"""``chimera ferret fork <session-id> [--last] [--all]`` — fork a session.

Forking copies an existing ferret eventlog session into a new directory
under ``~/.chimera/eventlog/`` with a fresh ``ferret-<UTC>-<uuid>`` id
and a ``parent_id`` annotation in the new ``summary.json``. The result
is a runnable session the user can resume with ``chimera ferret -p``
plus ``--resume <new-id>``.

Flag semantics
--------------

* Bare ``ferret fork <id>`` — fork that exact session id.
* ``ferret fork --last`` — fork the newest ferret session under cwd.
* ``ferret fork --all`` — fork the newest ferret session regardless of
  cwd (cross-project picker).
* Combining ``<id>`` with ``--last`` / ``--all`` is a usage error.

Exit codes
----------

* ``0`` — fork succeeded; new id printed on stdout.
* ``2`` — usage error or source session missing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from chimera.config.paths import store_path

__all__ = [
    "fork_session",
    "resolve_fork_source",
    "run_fork",
]


def _ferret_eventlog_root() -> Path:
    """Return ``~/.chimera/eventlog`` honoring ``Path.home()`` patches."""
    return store_path("eventlog")


def _new_session_id(prefix: str = "ferret-") -> str:
    """Mint a fresh ``ferret-<UTC>-<uuid8>`` directory name."""
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"{prefix}{now}-{suffix}"


def _list_ferret_sessions(
    eventlog_root: Path | None = None,
    *,
    cwd_filter: str | None = None,
) -> list[Path]:
    """Return ferret session directories newest-first.

    Args:
        eventlog_root: Override the eventlog root.
        cwd_filter: When non-empty, only return sessions whose
            ``summary.json`` records ``cwd == cwd_filter``. Useful for
            ``--last`` (cwd-scoped) vs. ``--all`` (cross-cwd) selection.
    """
    root = eventlog_root or _ferret_eventlog_root()
    if not root.exists() or not root.is_dir():
        return []
    sessions = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name.startswith("ferret-")),
        reverse=True,
    )
    if cwd_filter is None:
        return sessions
    matched: list[Path] = []
    for session in sessions:
        summary = session / "summary.json"
        if not summary.exists():
            continue
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and str(data.get("cwd", "")) == cwd_filter:
            matched.append(session)
    return matched


def resolve_fork_source(
    args: argparse.Namespace,
    *,
    eventlog_root: Path | None = None,
) -> tuple[Path | None, str | None]:
    """Resolve which session ``ferret fork`` should clone.

    Reads three slots off *args*:

    * ``sub_action`` — explicit session id from the cli positional.
    * ``last`` — boolean ``--last`` (newest under cwd).
    * ``serve_stop_all`` — boolean (the existing ``--all`` flag, see
      :mod:`chimera.ferret.cli`; reused for fork-all semantics).

    Returns:
        A tuple ``(session_path, error)``. On success, ``session_path``
        is the resolved directory and ``error`` is ``None``. On
        failure, ``session_path`` is ``None`` and ``error`` carries a
        usage / not-found message.
    """
    explicit = getattr(args, "sub_action", None) or getattr(args, "sub_target", None)
    use_last = bool(getattr(args, "last", False))
    use_all = bool(getattr(args, "serve_stop_all", False))

    if explicit and (use_last or use_all):
        return None, (
            "ferret fork: <session-id> conflicts with --last/--all; "
            "pass exactly one selector."
        )

    root = eventlog_root or _ferret_eventlog_root()
    if explicit:
        candidate = root / str(explicit)
        if not candidate.exists() or not candidate.is_dir():
            return None, f"ferret fork: source session not found: {explicit!r}"
        return candidate, None

    if use_last or use_all:
        cwd = os.path.abspath(getattr(args, "cwd", None) or os.getcwd())
        sessions = _list_ferret_sessions(
            root, cwd_filter=cwd if use_last and not use_all else None,
        )
        if not sessions:
            scope = "under cwd" if (use_last and not use_all) else "in eventlog"
            return None, f"ferret fork: no ferret sessions found {scope}."
        return sessions[0], None

    return None, (
        "ferret fork: missing <session-id>. "
        "Pass an id, --last, or --all."
    )


def fork_session(
    source: Path,
    *,
    eventlog_root: Path | None = None,
    new_id: str | None = None,
) -> Path:
    """Copy *source* into a new eventlog directory and return its path.

    The ``summary.json`` of the fork is rewritten with:

    * a fresh ``session_id`` matching the directory name;
    * a ``parent_id`` field pointing at the original;
    * the original ``forked_at`` timestamp set to the moment we forked.

    Every ``event-*.json`` file is copied byte-for-byte so the fork
    replays identically when resumed.

    Args:
        source: The source session directory.
        eventlog_root: Override the eventlog root for the destination.
        new_id: Optional explicit destination id. Defaults to a fresh
            :func:`_new_session_id`.

    Returns:
        The :class:`Path` of the new (forked) session directory.

    Raises:
        FileNotFoundError: When *source* doesn't exist.
        FileExistsError: When the destination id already exists.
    """
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(f"source session not found: {source}")

    root = eventlog_root or _ferret_eventlog_root()
    root.mkdir(parents=True, exist_ok=True)
    fork_id = new_id or _new_session_id()
    dest = root / fork_id
    if dest.exists():
        raise FileExistsError(f"fork destination already exists: {dest}")

    shutil.copytree(source, dest)

    # Rewrite the summary.json so the fork carries its own id and a
    # back-pointer to its parent. The original summary may be missing
    # or malformed — degrade gracefully so the copy still succeeds.
    summary_path = dest / "summary.json"
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["session_id"] = fork_id
    data["parent_id"] = source.name
    data["forked_at"] = datetime.now(timezone.utc).isoformat()
    summary_path.write_text(
        json.dumps(data, indent=2, sort_keys=True), encoding="utf-8",
    )
    return dest


def run_fork(args: argparse.Namespace) -> int:
    """Entry point for ``chimera ferret fork``.

    Returns:
        Process exit code (see module docstring).
    """
    source, error = resolve_fork_source(args)
    if error or source is None:
        sys.stderr.write(f"{error or 'ferret fork: unknown error'}\n")
        return 2
    try:
        new_dir = fork_session(source)
    except (FileNotFoundError, FileExistsError) as exc:
        sys.stderr.write(f"ferret fork: {exc}\n")
        return 2
    sys.stdout.write(
        f"[ferret fork] {source.name} -> {new_dir.name}\n"
    )
    sys.stdout.write(
        f"resume with: chimera ferret -p '<prompt>' --resume {new_dir.name}\n"
    )
    return 0

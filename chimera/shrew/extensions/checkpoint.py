"""Shrew file-snapshot checkpoint helper (W15-2 P2 / LITTLE-CODER GAP-EXT-5).

Small models are prone to over-eager Write tool calls that destroy
useful files (the `write-guard` invariant addresses one shape of this;
file checkpoints address the other). The upstream little-coder ships an
extension that snapshots a file's pre-edit contents to
``~/.little-coder/checkpoints/<session>/<sha>`` before each Write or
Edit so the operator can roll back without leaving the REPL.

This module is the shrew port. It is stdlib-only and persists snapshots
under ``~/.shrew/checkpoints/<session>/`` (XDG-friendly: respects
``$SHREW_CHECKPOINT_DIR`` when set). The on-disk layout is:

    ~/.shrew/checkpoints/
      <session_id>/
        <hash>.snapshot     -- bytes of the pre-edit file
        <hash>.meta         -- JSON: original_path, sha256, ctime, size

Restoration is by hash: the caller passes the hash they got back from
:func:`snapshot_file` to :func:`restore_file` to write the bytes back.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CheckpointInfo",
    "checkpoint_root",
    "list_checkpoints",
    "restore_file",
    "snapshot_file",
]


@dataclass(frozen=True)
class CheckpointInfo:
    """Metadata about a stored snapshot.

    Attributes:
        hash: 16-char prefix of the SHA-256 over the original file bytes;
            also doubles as the on-disk filename.
        original_path: Absolute path to the file at snapshot time.
        size: Original file size in bytes.
        ctime: Snapshot creation time (Unix epoch seconds).
        session_id: The session this snapshot belongs to.
    """

    hash: str
    original_path: str
    size: int
    ctime: float
    session_id: str


def checkpoint_root(session_id: str | None = None) -> Path:
    """Return the directory snapshots live under.

    Args:
        session_id: Optional session-scope. ``None`` returns the parent
            of every session's snapshot directory.

    Environment:
        ``SHREW_CHECKPOINT_DIR`` overrides the default
        ``~/.shrew/checkpoints``. Tests use this to stay hermetic.
    """
    base_env = os.environ.get("SHREW_CHECKPOINT_DIR")
    base = Path(base_env).expanduser() if base_env else Path.home() / ".shrew" / "checkpoints"
    if session_id is None:
        return base
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in session_id)
    return base / (safe or "default")


def _hash_bytes(data: bytes) -> str:
    """Return the 16-char SHA-256 prefix used for snapshot filenames."""
    return hashlib.sha256(data).hexdigest()[:16]


def snapshot_file(path: str | os.PathLike[str], session_id: str = "default") -> CheckpointInfo | None:
    """Persist *path*'s current bytes to the checkpoint store.

    Returns ``None`` when *path* does not exist (nothing to snapshot;
    the caller is about to write a brand-new file). Returns a
    :class:`CheckpointInfo` otherwise. Identical content under the same
    session is deduplicated — re-snapping the same bytes returns the
    same hash and avoids a second disk write.

    Args:
        path: File to snapshot. Must be readable.
        session_id: Session bucket the snapshot belongs to.
    """
    src = Path(path)
    if not src.exists() or src.is_dir():
        return None
    try:
        data = src.read_bytes()
    except OSError:
        return None
    digest = _hash_bytes(data)
    bucket = checkpoint_root(session_id)
    bucket.mkdir(parents=True, exist_ok=True)
    snap_path = bucket / f"{digest}.snapshot"
    meta_path = bucket / f"{digest}.meta"
    if not snap_path.exists():
        snap_path.write_bytes(data)
    info = CheckpointInfo(
        hash=digest,
        original_path=str(src.resolve()),
        size=len(data),
        ctime=time.time(),
        session_id=session_id,
    )
    if not meta_path.exists():
        meta_path.write_text(
            json.dumps(
                {
                    "hash": info.hash,
                    "original_path": info.original_path,
                    "size": info.size,
                    "ctime": info.ctime,
                    "session_id": info.session_id,
                },
                indent=2,
            )
        )
    return info


def restore_file(
    digest: str,
    *,
    session_id: str = "default",
    target: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Restore a snapshot by hash.

    Args:
        digest: The hash returned by :func:`snapshot_file`.
        session_id: Session bucket the snapshot lives under.
        target: Optional override for the restore destination. ``None``
            restores to the recorded ``original_path`` (default).

    Returns:
        The path the bytes were written to, or ``None`` when the
        snapshot is missing or unreadable.
    """
    bucket = checkpoint_root(session_id)
    snap_path = bucket / f"{digest}.snapshot"
    meta_path = bucket / f"{digest}.meta"
    if not snap_path.exists():
        return None
    if target is None:
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        original = meta.get("original_path")
        if not isinstance(original, str):
            return None
        dest = Path(original)
    else:
        dest = Path(target)
    try:
        data = snap_path.read_bytes()
    except OSError:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def list_checkpoints(session_id: str = "default") -> list[CheckpointInfo]:
    """Return every snapshot recorded for *session_id*, newest first."""
    bucket = checkpoint_root(session_id)
    if not bucket.exists():
        return []
    rows: list[CheckpointInfo] = []
    for meta_path in bucket.glob("*.meta"):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(
            CheckpointInfo(
                hash=str(meta.get("hash", "")),
                original_path=str(meta.get("original_path", "")),
                size=int(meta.get("size", 0)),
                ctime=float(meta.get("ctime", 0.0)),
                session_id=str(meta.get("session_id", session_id)),
            )
        )
    rows.sort(key=lambda r: r.ctime, reverse=True)
    return rows

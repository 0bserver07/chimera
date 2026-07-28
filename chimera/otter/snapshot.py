"""Git-shadow file snapshot store for otter ``/undo`` and ``/redo``.

Wave-3 (W13 G5) elevates otter's ``/undo`` from "rewind only the
conversation messages" to "rewind the conversation **and** any files the
agent touched in that turn". This module is the storage primitive that
makes the file part work without requiring the workspace itself to be a
git repo or a docker container.

Layout::

    ~/.chimera/snapshots/<session-id>/
        blobs/<sha256>                # content-addressed file payloads
        snaps/<snap-id>/manifest.json # {abs_path: sha256 | null}

Each per-turn manifest records every file that has been modified at
**any** point during the session — content-addressed via SHA-256 so
unchanged files share a single blob. Files that did not exist at snap
time are recorded as ``null`` so :meth:`FileSnapshotStore.restore` knows
to delete them on rewind (mirrors ``git checkout`` semantics for a file
that's been added since the target commit).

The store deliberately uses plain file copies rather than shelling out
to ``git``: otter sessions live in arbitrary working directories that
may or may not be a git repo, and the upstream coding agent's parallel
worktree dance is overkill for a per-session undo log.

Public API:

* :class:`FileSnapshotStore` — manages snaps for one session.
* :class:`FileSnapshot` — metadata for a single per-turn snapshot.
* :func:`default_snapshot_root` — ``~/.chimera/snapshots`` resolver.

Trademark hygiene: this module never names the upstream coding agent in
user-visible strings, per ``research/otter/SPEC.md``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from chimera.config.paths import store_path

__all__ = [
    "FileSnapshot",
    "FileSnapshotStore",
    "default_snapshot_root",
]


def default_snapshot_root() -> Path:
    """Return the default root for the file-snapshot store.

    Honors ``$CHIMERA_SNAPSHOT_ROOT`` for tests / sandboxed CI runs;
    falls back to ``~/.chimera/snapshots`` so production runs land in
    the standard Chimera state dir.
    """
    override = os.environ.get("CHIMERA_SNAPSHOT_ROOT")
    if override:
        return Path(override)
    return store_path("snapshots")


@dataclass
class FileSnapshot:
    """Metadata for a single per-turn file snapshot.

    Attributes:
        snap_id: Unique-within-store id for the snapshot. Used as the
            join key against the slash module's ``CheckpointInfo`` so
            ``/undo`` can find the matching file snap when it pops the
            undo stack.
        timestamp: Wall-clock time the snap was taken (``time.time()``).
        files: Mapping of absolute source path -> blob SHA-256 hex
            digest. ``None`` means "did not exist at snap time" — on
            restore, the target path is deleted so rewind correctly
            undoes a file that was created in the rewound turn.
    """

    snap_id: str
    timestamp: float
    files: dict[str, str | None] = field(default_factory=dict)


# Sentinel max-bytes guard: snapping a runaway 1GB log file silently
# would make ``/undo`` storage explode. We refuse to snap files above this
# size and fall back to recording ``None`` so the rewind still does the
# right thing for the on-disk file (delete-on-restore is conservative).
_MAX_SNAP_FILE_BYTES = 25 * 1024 * 1024  # 25 MiB


class FileSnapshotStore:
    """Per-session content-addressed file snapshot store.

    Each :class:`FileSnapshotStore` owns one directory under
    :func:`default_snapshot_root` keyed by ``session_id``. Snaps share a
    single ``blobs/`` directory so unchanged files across N turns cost
    ``O(1)`` storage (one shared blob) rather than ``O(N)``.

    Thread-safety: not thread-safe. The otter REPL drives snaps from the
    main thread between turns, so we avoid the locking cost. Callers
    sharing a store across threads should add their own mutex.
    """

    def __init__(
        self,
        session_id: str,
        *,
        root: Path | None = None,
    ) -> None:
        """Create or attach to the on-disk store for *session_id*.

        Args:
            session_id: Stable identifier for the session this store
                belongs to. Doubles as the on-disk subdirectory name —
                so callers should pass something filesystem-safe (the
                Chimera ``Session`` UUIDs already are).
            root: Override the snapshot root. Defaults to
                :func:`default_snapshot_root`. Tests pass ``tmp_path``
                here so they don't pollute the user's home dir.
        """
        base = (root or default_snapshot_root()).expanduser()
        self.session_id = session_id
        self.root: Path = base / session_id
        self.blobs: Path = self.root / "blobs"
        self.snaps_dir: Path = self.root / "snaps"
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.snaps_dir.mkdir(parents=True, exist_ok=True)
        # In-memory snap cache (keyed by snap_id). Populated lazily from
        # disk on first access — survives process restarts in case a
        # future feature wants to /undo across sessions.
        self._snaps: dict[str, FileSnapshot] = {}
        self._counter: int = 0

    # ------------------------------------------------------------------
    # snap
    # ------------------------------------------------------------------

    def snap(
        self,
        modified_files: Iterable[str | Path],
        *,
        snap_id: str | None = None,
    ) -> FileSnapshot:
        """Record current contents of *modified_files* under a fresh snap.

        Args:
            modified_files: Iterable of paths the agent modified during
                the turn (cumulative across the session is fine — the
                store is content-addressed, so unchanged files don't
                duplicate). Strings or :class:`~pathlib.Path` both work.
                Empty iterables produce an empty (still valid) snap so
                the caller can join against an undo stack uniformly.
            snap_id: Optional explicit id. When ``None`` we mint one
                from the internal counter + millisecond timestamp so
                snaps remain sortable even if two land in the same
                second (the upstream agent's git-worktree shadow does
                the same dance).

        Returns:
            The persisted :class:`FileSnapshot`.
        """
        if snap_id is None:
            self._counter += 1
            snap_id = f"snap-{self._counter}-{int(time.time() * 1000)}"

        snap_dir = self.snaps_dir / snap_id
        snap_dir.mkdir(parents=True, exist_ok=True)

        files: dict[str, str | None] = {}
        seen: set[str] = set()
        for raw in modified_files:
            try:
                path = Path(raw)
            except TypeError:
                # Defensive — never raise on malformed input.
                continue
            try:
                abs_path = str(path.resolve()) if not path.is_absolute() else str(path)
            except OSError:
                abs_path = str(path)
            if abs_path in seen:
                continue
            seen.add(abs_path)

            files[abs_path] = self._capture_blob(path)

        timestamp = time.time()
        manifest = {
            "snap_id": snap_id,
            "timestamp": timestamp,
            "files": files,
        }
        (snap_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        snap = FileSnapshot(snap_id=snap_id, timestamp=timestamp, files=dict(files))
        self._snaps[snap_id] = snap
        return snap

    def _capture_blob(self, path: Path) -> str | None:
        """Copy *path* into the blob store, returning the SHA-256 digest.

        Returns ``None`` when the file does not exist (so the restore
        path can ``unlink`` it) or when it exceeds
        :data:`_MAX_SNAP_FILE_BYTES` (so a runaway log file doesn't
        balloon ``~/.chimera``). Symlinks are followed — the upstream
        agent's worktree shadow does the same.
        """
        try:
            if not path.exists():
                return None
            if not path.is_file():
                # Directories, sockets, etc — not something /undo can
                # meaningfully restore. Treat as "did not exist".
                return None
            stat = path.stat()
        except OSError:
            return None
        if stat.st_size > _MAX_SNAP_FILE_BYTES:
            return None

        try:
            content = path.read_bytes()
        except OSError:
            return None

        digest = hashlib.sha256(content).hexdigest()
        blob_path = self.blobs / digest
        if not blob_path.exists():
            try:
                blob_path.write_bytes(content)
            except OSError:
                return None
        return digest

    # ------------------------------------------------------------------
    # restore
    # ------------------------------------------------------------------

    def restore(self, snap_id: str) -> list[str]:
        """Restore every file in the named snap to its captured contents.

        Args:
            snap_id: The id returned by :meth:`snap`.

        Returns:
            The list of absolute paths actually mutated (written or
            deleted). A best-effort restore: missing blobs / unwritable
            targets are silently skipped so a partial filesystem failure
            doesn't crash the REPL mid-undo.
        """
        snap = self._load(snap_id)
        if snap is None:
            return []
        restored: list[str] = []
        for abs_path, digest in snap.files.items():
            target = Path(abs_path)
            if digest is None:
                # File didn't exist at snap time — undo any creation by
                # removing it now.
                if target.exists() and target.is_file():
                    try:
                        target.unlink()
                        restored.append(abs_path)
                    except OSError:
                        continue
                continue

            blob_path = self.blobs / digest
            if not blob_path.is_file():
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                # Use atomic-ish write: write to a sibling tempfile then
                # rename. Avoids a half-written file if the process is
                # killed mid-restore.
                tmp = target.with_suffix(target.suffix + ".otter-tmp")
                shutil.copy2(blob_path, tmp)
                os.replace(tmp, target)
                restored.append(abs_path)
            except OSError:
                continue
        return restored

    # ------------------------------------------------------------------
    # housekeeping
    # ------------------------------------------------------------------

    def has_snap(self, snap_id: str) -> bool:
        """Return ``True`` if *snap_id* exists in this store (in-memory or on disk)."""
        if snap_id in self._snaps:
            return True
        return (self.snaps_dir / snap_id / "manifest.json").is_file()

    def list_snaps(self) -> list[str]:
        """Return all known snap ids in lexicographic order.

        Combines the in-memory cache with on-disk snaps so a freshly
        attached store sees prior runs' snaps too.
        """
        on_disk = [
            p.name
            for p in self.snaps_dir.iterdir()
            if p.is_dir() and (p / "manifest.json").is_file()
        ] if self.snaps_dir.is_dir() else []
        return sorted(set(self._snaps) | set(on_disk))

    def discard(self, snap_id: str) -> bool:
        """Drop a snap from the in-memory cache and remove its on-disk dir.

        Blobs are NOT garbage-collected — they may still be referenced
        by other snaps. Call :meth:`gc_blobs` to reclaim space once the
        whole undo/redo state is invalidated.

        Args:
            snap_id: The id to discard.

        Returns:
            ``True`` if the snap existed and was removed, ``False``
            otherwise (best-effort: filesystem errors are swallowed).
        """
        existed = snap_id in self._snaps
        self._snaps.pop(snap_id, None)
        snap_dir = self.snaps_dir / snap_id
        if snap_dir.exists():
            existed = True
            try:
                shutil.rmtree(snap_dir)
            except OSError:
                return False
        return existed

    def gc_blobs(self) -> int:
        """Garbage-collect blobs no longer referenced by any snap manifest.

        Walks every manifest under :attr:`snaps_dir`, builds the set of
        live blob digests, and unlinks every other file in
        :attr:`blobs`. Best-effort: filesystem errors are swallowed so
        a partial GC never crashes the REPL.

        Returns:
            The number of blob files removed.
        """
        live: set[str] = set()
        if self.snaps_dir.is_dir():
            for snap_dir in self.snaps_dir.iterdir():
                manifest = snap_dir / "manifest.json"
                if not manifest.is_file():
                    continue
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                files = data.get("files") or {}
                for digest in files.values():
                    if isinstance(digest, str):
                        live.add(digest)
        removed = 0
        if self.blobs.is_dir():
            for blob in self.blobs.iterdir():
                if not blob.is_file():
                    continue
                if blob.name in live:
                    continue
                try:
                    blob.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    def clear(self) -> None:
        """Wipe the whole on-disk store for this session.

        Removes both ``blobs/`` and ``snaps/``. Used by ``/new`` (and by
        the REPL on session teardown) so a fresh session doesn't inherit
        a stale shadow from its predecessor.
        """
        self._snaps.clear()
        self._counter = 0
        for sub in (self.snaps_dir, self.blobs):
            if sub.exists():
                try:
                    shutil.rmtree(sub)
                except OSError:
                    pass
        self.snaps_dir.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _load(self, snap_id: str) -> FileSnapshot | None:
        """Return the cached or freshly loaded snap for *snap_id*."""
        cached = self._snaps.get(snap_id)
        if cached is not None:
            return cached
        manifest = self.snaps_dir / snap_id / "manifest.json"
        if not manifest.is_file():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        files = data.get("files") or {}
        if not isinstance(files, dict):
            return None
        # Coerce values to ``str | None`` defensively.
        coerced: dict[str, str | None] = {}
        for k, v in files.items():
            if v is None:
                coerced[str(k)] = None
            elif isinstance(v, str):
                coerced[str(k)] = v
        snap = FileSnapshot(
            snap_id=str(data.get("snap_id") or snap_id),
            timestamp=float(data.get("timestamp") or 0.0),
            files=coerced,
        )
        self._snaps[snap_id] = snap
        return snap

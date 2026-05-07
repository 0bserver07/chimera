"""Tests for the otter ``/undo`` and ``/redo`` file-rewind contract (G5).

W13-G5 elevates ``/undo`` from "rewind only the conversation messages"
to "rewind the conversation **and** any files the agent touched in that
turn". The on-disk shadow lives in
:mod:`chimera.otter.snapshot.FileSnapshotStore` and rides alongside the
existing checkpoint stack defined in :mod:`chimera.otter.slash`.

Contract under test (mirrors ``research/W13-G5-FILE-UNDO.md``):

* :class:`FileSnapshotStore` correctly captures and restores file
  contents, deletes files that were created since the snap, and round-
  trips bytes via the content-addressed blob store.
* :func:`chimera.otter.slash.snapshot_after_turn` reads modified files
  off ``session._otter_file_tracker`` (the test side-channel that maps
  to :class:`~chimera.core.file_tracker.FileTracker` in production) and
  records a :class:`FileSnapshot` on the per-session undo state.
* :func:`chimera.otter.slash.cmd_undo` restores the file contents
  attached to the new top-of-stack checkpoint.
* :func:`chimera.otter.slash.cmd_redo` re-applies the file contents.
* ``/undo --steps N`` and bare ``/undo 3`` rewind multiple turns at once.

The session + env fakes here are intentionally tiny duck types — the
contract is the file state machine, not Session/Environment integration.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from chimera.otter.slash import (
    clear_undo_state,
    cmd_redo,
    cmd_undo,
    collect_modified_files,
    get_file_snapshot_store,
    get_undo_state,
    snapshot_after_turn,
)
from chimera.otter.snapshot import (
    FileSnapshot,
    FileSnapshotStore,
    default_snapshot_root,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeFileTracker:
    """Tiny duck-typed FileTracker that mirrors the production attribute set."""

    def __init__(self) -> None:
        self.modified_files: list[str] = []
        self.read_files: list[str] = []

    def record_modified(self, path: str | Path) -> None:
        s = str(path)
        if s not in self.modified_files:
            self.modified_files.append(s)


class _FakeContext:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []


class _FakeSession:
    """Minimal session with a writable :attr:`context.messages` list.

    Carries an explicit ``session_id`` so the file shadow store has a
    deterministic on-disk subdirectory under ``tmp_path``. Tests pass
    ``_otter_snapshot_root`` so the store stays under the temp dir.
    """

    def __init__(self, session_id: str = "test-session") -> None:
        self.session_id = session_id
        self.context = _FakeContext()
        self._otter_file_tracker = _FakeFileTracker()
        self._otter_snapshot_root: Path | None = None


class _CapturePrinter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


def _make_session(tmp_path: Path, session_id: str = "test-session") -> _FakeSession:
    """Wire a fake session whose file shadow lives under *tmp_path*."""
    sess = _FakeSession(session_id=session_id)
    sess._otter_snapshot_root = tmp_path / "snapshots"
    return sess


def _drive_turn(
    session: _FakeSession,
    env: Any,
    *,
    user: str,
    assistant: str,
    file_writes: dict[str, str] | None = None,
) -> None:
    """Synthesise an assistant turn that may modify some files.

    The fake ``FileTracker`` is updated to mirror what real tool calls
    would log, then ``snapshot_after_turn`` is invoked just like the
    REPL does after each assistant reply.
    """
    session.context.messages.append({"role": "user", "content": user})
    session.context.messages.append({"role": "assistant", "content": assistant})
    if file_writes:
        for path, content in file_writes.items():
            Path(path).write_text(content)
            session._otter_file_tracker.record_modified(path)
    snapshot_after_turn(session, env)


# ---------------------------------------------------------------------------
# FileSnapshotStore unit tests
# ---------------------------------------------------------------------------


def test_default_snapshot_root_honors_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``CHIMERA_SNAPSHOT_ROOT`` must override the default ``~/.chimera``."""
    target = tmp_path / "custom-root"
    monkeypatch.setenv("CHIMERA_SNAPSHOT_ROOT", str(target))
    assert default_snapshot_root() == target


def test_default_snapshot_root_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_SNAPSHOT_ROOT", raising=False)
    assert default_snapshot_root() == Path.home() / ".chimera" / "snapshots"


def test_store_captures_and_restores_a_modified_file(tmp_path: Path) -> None:
    store = FileSnapshotStore(session_id="s1", root=tmp_path)
    target = tmp_path / "code.py"
    target.write_text("v1")

    snap = store.snap([target])

    target.write_text("v2")
    assert target.read_text() == "v2"

    restored = store.restore(snap.snap_id)
    assert str(target.resolve()) in [str(Path(p).resolve()) for p in restored]
    assert target.read_text() == "v1"


def test_store_records_missing_file_as_none_and_restore_unlinks(tmp_path: Path) -> None:
    """A snap of a not-yet-existing path records ``None``; restore deletes it.

    This is the contract that lets ``/undo`` rewind a file the agent
    *created* in the rewound turn.
    """
    store = FileSnapshotStore(session_id="s1", root=tmp_path)
    new_file = tmp_path / "fresh.txt"
    assert not new_file.exists()

    snap = store.snap([new_file])
    assert snap.files[str(new_file.resolve())] is None

    new_file.write_text("created in turn 2")
    store.restore(snap.snap_id)
    assert not new_file.exists(), "restore must unlink files that didn't exist at snap time"


def test_store_uses_content_addressed_dedup(tmp_path: Path) -> None:
    """Two snaps of the same content share a single blob on disk."""
    store = FileSnapshotStore(session_id="s1", root=tmp_path)
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("identical")
    b.write_text("identical")

    store.snap([a])
    store.snap([b])

    blobs = list(store.blobs.iterdir())
    assert len(blobs) == 1, "identical content should share one blob"


def test_store_skips_files_above_max_size(tmp_path: Path) -> None:
    """Runaway log files must not balloon the shadow store."""
    from chimera.otter import snapshot as _snap_mod

    store = FileSnapshotStore(session_id="s1", root=tmp_path)
    huge = tmp_path / "huge.bin"
    # Write just over the cap so the snap records None instead of capturing bytes.
    huge.write_bytes(b"x" * (_snap_mod._MAX_SNAP_FILE_BYTES + 1))

    snap = store.snap([huge])
    assert snap.files[str(huge.resolve())] is None


def test_store_discard_removes_snap_dir(tmp_path: Path) -> None:
    store = FileSnapshotStore(session_id="s1", root=tmp_path)
    f = tmp_path / "x.txt"
    f.write_text("hi")
    snap = store.snap([f])
    assert (store.snaps_dir / snap.snap_id).is_dir()

    assert store.discard(snap.snap_id) is True
    assert not (store.snaps_dir / snap.snap_id).exists()


def test_store_gc_blobs_only_unlinks_unreferenced(tmp_path: Path) -> None:
    store = FileSnapshotStore(session_id="s1", root=tmp_path)
    a = tmp_path / "a.txt"
    a.write_text("alpha")
    snap_a = store.snap([a])

    b = tmp_path / "b.txt"
    b.write_text("beta")
    snap_b = store.snap([b])

    # GC with both snaps live should not remove anything.
    assert store.gc_blobs() == 0
    assert len(list(store.blobs.iterdir())) == 2

    # Discarding snap_b leaves its blob orphaned -> GC reclaims it.
    store.discard(snap_b.snap_id)
    removed = store.gc_blobs()
    assert removed == 1
    assert len(list(store.blobs.iterdir())) == 1
    # The remaining blob is still there for snap_a.
    assert store.has_snap(snap_a.snap_id)


def test_store_round_trips_via_disk_after_reattach(tmp_path: Path) -> None:
    """A fresh store rooted at the same path can read prior snaps from disk."""
    s1 = FileSnapshotStore(session_id="reattach", root=tmp_path)
    f = tmp_path / "doc.md"
    f.write_text("before")
    snap = s1.snap([f])
    f.write_text("after")

    s2 = FileSnapshotStore(session_id="reattach", root=tmp_path)
    assert s2.has_snap(snap.snap_id)
    s2.restore(snap.snap_id)
    assert f.read_text() == "before"


def test_store_clear_wipes_session_dir(tmp_path: Path) -> None:
    store = FileSnapshotStore(session_id="s1", root=tmp_path)
    f = tmp_path / "x.txt"
    f.write_text("hi")
    store.snap([f])

    store.clear()
    assert not list(store.snaps_dir.iterdir())
    assert not list(store.blobs.iterdir())


# ---------------------------------------------------------------------------
# slash.collect_modified_files
# ---------------------------------------------------------------------------


def test_collect_modified_files_reads_from_explicit_session_attr(tmp_path: Path) -> None:
    sess = _make_session(tmp_path)
    sess._otter_file_tracker.record_modified("/abs/path/to/foo.py")
    sess._otter_file_tracker.record_modified("/abs/path/to/bar.py")

    result = collect_modified_files(sess, env=None)
    assert result == ["/abs/path/to/foo.py", "/abs/path/to/bar.py"]


def test_collect_modified_files_empty_when_no_tracker() -> None:
    """A bare object with no tracker yields an empty list (not an error)."""

    class _Bare:
        pass

    assert collect_modified_files(_Bare(), env=None) == []


def test_collect_modified_files_dedups_across_surfaces(tmp_path: Path) -> None:
    sess = _make_session(tmp_path)
    sess._otter_file_tracker.record_modified("/abs/x.py")
    # Even if a different surface listed the same file, dedup keeps order.
    result = collect_modified_files(sess, env=None)
    assert result == ["/abs/x.py"]


# ---------------------------------------------------------------------------
# Slash command end-to-end: modify, /undo, /redo
# ---------------------------------------------------------------------------


def test_undo_rewinds_a_modified_file(tmp_path: Path) -> None:
    """Spec scenario: write file v1, /undo, file is back to its pre-turn state."""
    sess = _make_session(tmp_path)
    out = _CapturePrinter()
    target = tmp_path / "code.py"
    target.write_text("baseline")

    try:
        # Baseline snap captures pre-turn state. Mark the file as modified
        # so the snap captures its baseline content.
        sess._otter_file_tracker.record_modified(str(target))
        snapshot_after_turn(sess, env=None)

        # Turn 1: agent edits the file to v1.
        _drive_turn(
            sess, env=None,
            user="please edit",
            assistant="done",
            file_writes={str(target): "v1"},
        )
        assert target.read_text() == "v1"

        # /undo rewinds to baseline content.
        cmd_undo(sess, None, "", out)
        assert target.read_text() == "baseline"
        assert any("/undo" in line for line in out.lines)
        assert any("files restored" in line for line in out.lines)
    finally:
        clear_undo_state(sess)


def test_redo_reapplies_file_modification(tmp_path: Path) -> None:
    """Spec scenario: write v1, /undo, /redo, file is back at v1."""
    sess = _make_session(tmp_path)
    out = _CapturePrinter()
    target = tmp_path / "code.py"
    target.write_text("baseline")

    try:
        sess._otter_file_tracker.record_modified(str(target))
        snapshot_after_turn(sess, env=None)

        _drive_turn(
            sess, env=None,
            user="edit",
            assistant="done",
            file_writes={str(target): "v1"},
        )

        cmd_undo(sess, None, "", out)
        assert target.read_text() == "baseline"

        cmd_redo(sess, None, "", out)
        assert target.read_text() == "v1"
        assert any("/redo" in line for line in out.lines)
    finally:
        clear_undo_state(sess)


def test_undo_steps_multi_rewind(tmp_path: Path) -> None:
    """``/undo --steps 2`` rewinds two turns in a single command."""
    sess = _make_session(tmp_path)
    out = _CapturePrinter()
    target = tmp_path / "doc.md"
    target.write_text("v0")

    try:
        sess._otter_file_tracker.record_modified(str(target))
        snapshot_after_turn(sess, env=None)

        _drive_turn(sess, None, user="t1", assistant="a1", file_writes={str(target): "v1"})
        _drive_turn(sess, None, user="t2", assistant="a2", file_writes={str(target): "v2"})

        assert target.read_text() == "v2"

        cmd_undo(sess, None, "--steps 2", out)
        assert target.read_text() == "v0"
        # Messages should also have rewound to baseline (empty conversation).
        assert sess.context.messages == []
    finally:
        clear_undo_state(sess)


def test_undo_bare_integer_steps(tmp_path: Path) -> None:
    """``/undo 2`` (no flag) is equivalent to ``--steps 2``."""
    sess = _make_session(tmp_path)
    out = _CapturePrinter()
    target = tmp_path / "doc.md"
    target.write_text("v0")

    try:
        sess._otter_file_tracker.record_modified(str(target))
        snapshot_after_turn(sess, env=None)

        _drive_turn(sess, None, user="t1", assistant="a1", file_writes={str(target): "v1"})
        _drive_turn(sess, None, user="t2", assistant="a2", file_writes={str(target): "v2"})

        cmd_undo(sess, None, "2", out)
        assert target.read_text() == "v0"
    finally:
        clear_undo_state(sess)


def test_redo_steps_multi_replay(tmp_path: Path) -> None:
    sess = _make_session(tmp_path)
    out = _CapturePrinter()
    target = tmp_path / "doc.md"
    target.write_text("v0")

    try:
        sess._otter_file_tracker.record_modified(str(target))
        snapshot_after_turn(sess, env=None)

        _drive_turn(sess, None, user="t1", assistant="a1", file_writes={str(target): "v1"})
        _drive_turn(sess, None, user="t2", assistant="a2", file_writes={str(target): "v2"})

        cmd_undo(sess, None, "--steps 2", out)
        assert target.read_text() == "v0"

        cmd_redo(sess, None, "--steps 2", out)
        assert target.read_text() == "v2"
    finally:
        clear_undo_state(sess)


def test_undo_with_invalid_steps_falls_back_to_one(tmp_path: Path) -> None:
    """Garbage args degrade to a 1-step undo rather than crashing."""
    sess = _make_session(tmp_path)
    out = _CapturePrinter()
    target = tmp_path / "x.txt"
    target.write_text("v0")

    try:
        sess._otter_file_tracker.record_modified(str(target))
        snapshot_after_turn(sess, env=None)

        _drive_turn(sess, None, user="t1", assistant="a1", file_writes={str(target): "v1"})
        _drive_turn(sess, None, user="t2", assistant="a2", file_writes={str(target): "v2"})

        cmd_undo(sess, None, "--step three", out)  # malformed
        # Falls back to 1 step -> rewinds to v1.
        assert target.read_text() == "v1"
    finally:
        clear_undo_state(sess)


def test_new_turn_after_undo_invalidates_redo_files(tmp_path: Path) -> None:
    """A fresh turn after /undo must drop the orphaned redo file shadow.

    Branching the conversation invalidates the redo path; the file
    snapshots bound to the orphaned redo entries are GC'd so the shadow
    store doesn't grow without bound across long REPL sessions.
    """
    sess = _make_session(tmp_path)
    out = _CapturePrinter()
    target = tmp_path / "doc.md"
    target.write_text("v0")

    try:
        sess._otter_file_tracker.record_modified(str(target))
        snapshot_after_turn(sess, env=None)

        _drive_turn(sess, None, user="t1", assistant="a1", file_writes={str(target): "v1"})
        _drive_turn(sess, None, user="t2", assistant="a2", file_writes={str(target): "v2"})

        cmd_undo(sess, None, "", out)
        state = get_undo_state(sess)
        assert len(state.redo_stack) == 1

        # Branch with a fresh turn — the redo entry's file snap should be
        # GC'd.
        store = get_file_snapshot_store(sess)
        assert store is not None
        snaps_before_branch = set(store.list_snaps())
        _drive_turn(
            sess, None,
            user="t2-alt", assistant="a2-alt",
            file_writes={str(target): "v2-alt"},
        )

        snaps_after_branch = set(store.list_snaps())
        # At least one orphaned redo snap must have been discarded.
        assert snaps_before_branch - snaps_after_branch, (
            "fresh turn must discard at least one orphaned redo snap"
        )
        # /redo is now a no-op.
        out2 = _CapturePrinter()
        cmd_redo(sess, None, "", out2)
        assert any("nothing to redo" in line for line in out2.lines)
    finally:
        clear_undo_state(sess)


def test_undo_with_no_file_tracker_still_rewinds_messages(tmp_path: Path) -> None:
    """Sessions without a tracker keep the legacy message-only undo working."""
    sess = _make_session(tmp_path)
    sess._otter_file_tracker = _FakeFileTracker()  # empty tracker
    out = _CapturePrinter()

    try:
        snapshot_after_turn(sess, env=None)
        sess.context.messages.append({"role": "user", "content": "hi"})
        sess.context.messages.append({"role": "assistant", "content": "hello"})
        snapshot_after_turn(sess, env=None)

        cmd_undo(sess, None, "", out)
        assert sess.context.messages == []
    finally:
        clear_undo_state(sess)


def test_clear_undo_state_wipes_file_shadow(tmp_path: Path) -> None:
    sess = _make_session(tmp_path)
    target = tmp_path / "x.txt"
    target.write_text("v0")
    sess._otter_file_tracker.record_modified(str(target))
    snapshot_after_turn(sess, env=None)

    state = get_undo_state(sess)
    assert state.file_store is not None
    snap_dir = state.file_store.root
    assert snap_dir.exists()

    clear_undo_state(sess)

    # After clear, the on-disk shadow's snap + blob dirs should be empty.
    # (clear() recreates empty subdirs so a re-attach doesn't ENOENT.)
    assert not list((snap_dir / "snaps").iterdir())
    assert not list((snap_dir / "blobs").iterdir())


def test_get_file_snapshot_store_is_idempotent(tmp_path: Path) -> None:
    """Repeated calls return the same store instance on the same session."""
    sess = _make_session(tmp_path)
    s1 = get_file_snapshot_store(sess)
    s2 = get_file_snapshot_store(sess)
    assert s1 is s2
    clear_undo_state(sess)


def test_file_snapshot_dataclass_round_trips_metadata() -> None:
    """``FileSnapshot`` exposes ``snap_id`` / ``timestamp`` / ``files``."""
    snap = FileSnapshot(
        snap_id="manual",
        timestamp=1234.5,
        files={"/a.py": "deadbeef", "/missing.py": None},
    )
    assert snap.snap_id == "manual"
    assert snap.timestamp == 1234.5
    assert snap.files["/missing.py"] is None
    # deepcopy must be safe (the slash module deepcopies state).
    cloned = copy.deepcopy(snap)
    assert cloned.files == snap.files
    assert cloned is not snap

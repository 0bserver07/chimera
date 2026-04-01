"""Tests for chimera.core.snapshot — Phase 9 Snapshot/Revert system."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from chimera.core.snapshot import FileState, Snapshot, SnapshotManager


@pytest.mark.asyncio
async def test_take_snapshot():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a file
        Path(tmpdir, "hello.py").write_text("print('hello')")

        mgr = SnapshotManager(Path(tmpdir))
        snap = await mgr.take(turn=1, modified_files=["hello.py"])

        assert snap.turn == 1
        assert "hello.py" in snap.file_states
        assert snap.file_states["hello.py"].content == b"print('hello')"


@pytest.mark.asyncio
async def test_revert_restores_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir, "hello.py")

        # Original content
        path.write_text("v1")
        mgr = SnapshotManager(Path(tmpdir))
        await mgr.take(turn=1, modified_files=["hello.py"])

        # Agent modifies
        path.write_text("v2 — broken")
        await mgr.take(turn=2, modified_files=["hello.py"])

        # Revert to turn 1
        restored = await mgr.revert(to_turn=1)
        assert "hello.py" in restored
        assert path.read_text() == "v1"


@pytest.mark.asyncio
async def test_revert_file_single():
    with tempfile.TemporaryDirectory() as tmpdir:
        a = Path(tmpdir, "a.py")
        b = Path(tmpdir, "b.py")
        a.write_text("a_v1")
        b.write_text("b_v1")

        mgr = SnapshotManager(Path(tmpdir))
        await mgr.take(turn=1, modified_files=["a.py", "b.py"])

        a.write_text("a_v2")
        b.write_text("b_v2")
        await mgr.take(turn=2, modified_files=["a.py", "b.py"])

        # Revert only a.py
        ok = await mgr.revert_file("a.py", to_turn=1)
        assert ok
        assert a.read_text() == "a_v1"
        assert b.read_text() == "b_v2"  # b unchanged


@pytest.mark.asyncio
async def test_revert_deleted_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir, "temp.py")
        path.write_text("content")

        mgr = SnapshotManager(Path(tmpdir))
        await mgr.take(turn=1, modified_files=["temp.py"])

        # Delete the file
        os.unlink(path)
        await mgr.take(turn=2, modified_files=["temp.py"])

        # Revert to turn 1 — file should be recreated
        await mgr.revert(to_turn=1)
        assert path.exists()
        assert path.read_text() == "content"


@pytest.mark.asyncio
async def test_diff_between_turns():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir, "hello.py")
        path.write_text("line1\nline2\n")

        mgr = SnapshotManager(Path(tmpdir))
        await mgr.take(turn=1, modified_files=["hello.py"])

        path.write_text("line1\nmodified\nline3\n")
        await mgr.take(turn=2, modified_files=["hello.py"])

        diff_text = await mgr.diff(from_turn=1, to_turn=2)
        assert "-line2" in diff_text
        assert "+modified" in diff_text


@pytest.mark.asyncio
async def test_list_snapshots():
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "a.py").write_text("a")
        mgr = SnapshotManager(Path(tmpdir))
        await mgr.take(turn=1, modified_files=["a.py"])
        await mgr.take(turn=2, modified_files=["a.py"])

        snaps = mgr.list_snapshots()
        assert len(snaps) == 2
        assert snaps[0].turn == 1
        assert snaps[1].turn == 2


@pytest.mark.asyncio
async def test_get_snapshot():
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "a.py").write_text("a")
        mgr = SnapshotManager(Path(tmpdir))
        await mgr.take(turn=1, modified_files=["a.py"])

        snap = mgr.get_snapshot(1)
        assert snap is not None
        assert snap.turn == 1

        assert mgr.get_snapshot(99) is None


@pytest.mark.asyncio
async def test_revert_nonexistent_turn():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = SnapshotManager(Path(tmpdir))
        restored = await mgr.revert(to_turn=99)
        assert restored == []  # Nothing to revert

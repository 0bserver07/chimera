"""Tests for chimera.hooks.file_watcher — FileWatcher emitting CWD_CHANGED and FILE_CHANGED."""
from __future__ import annotations

import time

import pytest

from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent
from chimera.hooks.file_watcher import FileWatcher
from chimera.hooks.hook_types import HookOutput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_recording_emitter() -> tuple[HookEmitter, list[tuple[HookEvent, dict]]]:
    """Return an emitter that records every (event, kwargs) it receives."""
    recordings: list[tuple[HookEvent, dict]] = []


    async def recording_emit(self, event, **kwargs):
        recordings.append((event, kwargs))
        return HookOutput()

    emitter = HookEmitter()
    emitter.emit = recording_emit.__get__(emitter, HookEmitter)
    return emitter, recordings


# ---------------------------------------------------------------------------
# Tests: check_cwd
# ---------------------------------------------------------------------------


class TestCheckCwd:
    @pytest.mark.asyncio
    async def test_cwd_changed_fires_on_change(self):
        emitter, recordings = _make_recording_emitter()
        watcher = FileWatcher(emitter=emitter)

        await watcher.check_cwd("/first/dir")
        assert len(recordings) == 0  # First call sets initial, no event

        await watcher.check_cwd("/second/dir")
        assert len(recordings) == 1
        event, kwargs = recordings[0]
        assert event == HookEvent.CWD_CHANGED
        assert kwargs["tool_input"]["old"] == "/first/dir"
        assert kwargs["tool_input"]["new"] == "/second/dir"

    @pytest.mark.asyncio
    async def test_cwd_no_event_when_same(self):
        emitter, recordings = _make_recording_emitter()
        watcher = FileWatcher(emitter=emitter)

        await watcher.check_cwd("/same/dir")
        await watcher.check_cwd("/same/dir")
        assert len(recordings) == 0

    @pytest.mark.asyncio
    async def test_cwd_no_event_on_first_call(self):
        emitter, recordings = _make_recording_emitter()
        watcher = FileWatcher(emitter=emitter)

        await watcher.check_cwd("/initial")
        assert len(recordings) == 0

    @pytest.mark.asyncio
    async def test_cwd_no_emitter(self):
        """FileWatcher without emitter should not raise."""
        watcher = FileWatcher(emitter=None)
        await watcher.check_cwd("/a")
        await watcher.check_cwd("/b")
        # No error is success


# ---------------------------------------------------------------------------
# Tests: check_files
# ---------------------------------------------------------------------------


class TestCheckFiles:
    @pytest.mark.asyncio
    async def test_file_changed_fires_on_mtime_change(self, tmp_path):
        emitter, recordings = _make_recording_emitter()
        watcher = FileWatcher(emitter=emitter)

        test_file = tmp_path / "watched.txt"
        test_file.write_text("v1")

        # First check — establishes baseline, no event
        await watcher.check_files([str(test_file)])
        assert len(recordings) == 0

        # Modify file (force mtime change)
        time.sleep(0.05)
        test_file.write_text("v2")

        # Second check — should fire FILE_CHANGED
        await watcher.check_files([str(test_file)])
        assert len(recordings) == 1
        event, kwargs = recordings[0]
        assert event == HookEvent.FILE_CHANGED
        assert kwargs["tool_input"]["path"] == str(test_file)

    @pytest.mark.asyncio
    async def test_file_unchanged_no_event(self, tmp_path):
        emitter, recordings = _make_recording_emitter()
        watcher = FileWatcher(emitter=emitter)

        test_file = tmp_path / "stable.txt"
        test_file.write_text("content")

        await watcher.check_files([str(test_file)])
        await watcher.check_files([str(test_file)])
        assert len(recordings) == 0

    @pytest.mark.asyncio
    async def test_file_nonexistent_no_error(self):
        emitter, recordings = _make_recording_emitter()
        watcher = FileWatcher(emitter=emitter)

        await watcher.check_files(["/nonexistent/file.txt"])
        assert len(recordings) == 0

    @pytest.mark.asyncio
    async def test_file_no_emitter(self, tmp_path):
        """FileWatcher without emitter should not raise on check_files."""
        watcher = FileWatcher(emitter=None)
        test_file = tmp_path / "f.txt"
        test_file.write_text("data")
        await watcher.check_files([str(test_file)])


# ---------------------------------------------------------------------------
# Tests: track
# ---------------------------------------------------------------------------


class TestTrack:
    def test_track_records_mtime(self, tmp_path):
        watcher = FileWatcher()
        test_file = tmp_path / "tracked.txt"
        test_file.write_text("content")

        watcher.track(str(test_file))
        assert str(test_file) in watcher._known_mtimes

    def test_track_nonexistent_no_error(self):
        watcher = FileWatcher()
        watcher.track("/nonexistent/file.txt")
        assert "/nonexistent/file.txt" not in watcher._known_mtimes

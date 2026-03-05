"""Tests for checkpoint manager."""
from __future__ import annotations

import tempfile
import time

import pytest

from chimera.checkpoints import CheckpointInfo, CheckpointManager
from chimera.env.local import LocalEnvironment


@pytest.fixture
def env_and_manager():
    with tempfile.TemporaryDirectory() as tmp:
        env = LocalEnvironment(workdir=tmp)
        env.setup()
        mgr = CheckpointManager(env)
        yield env, mgr
        env.cleanup()


class TestCheckpointManager:
    def test_create_checkpoint(self, env_and_manager):
        env, mgr = env_and_manager
        info = mgr.create("initial", "before changes")
        assert info.name == "initial"
        assert info.description == "before changes"
        assert info.id  # non-empty

    def test_auto_name(self, env_and_manager):
        env, mgr = env_and_manager
        info = mgr.create()
        assert info.name == "checkpoint-1"
        info2 = mgr.create()
        assert info2.name == "checkpoint-2"

    def test_list_checkpoints(self, env_and_manager):
        env, mgr = env_and_manager
        mgr.create("a")
        mgr.create("b")
        cps = mgr.list_checkpoints()
        assert len(cps) == 2
        assert cps[0].name == "a"
        assert cps[1].name == "b"

    def test_restore_by_name(self, env_and_manager):
        env, mgr = env_and_manager
        env.write_file("test.txt", "original")
        mgr.create("before")
        env.write_file("test.txt", "modified")
        mgr.restore_by_name("before")
        content = env.read_file("test.txt")
        assert content == "original"

    def test_restore_by_name_not_found(self, env_and_manager):
        env, mgr = env_and_manager
        with pytest.raises(KeyError):
            mgr.restore_by_name("nonexistent")

    def test_restore_by_id(self, env_and_manager):
        env, mgr = env_and_manager
        env.write_file("f.txt", "v1")
        info = mgr.create("cp1")
        env.write_file("f.txt", "v2")
        mgr.restore_by_id(info.id)
        assert env.read_file("f.txt") == "v1"

    def test_restore_by_id_not_found(self, env_and_manager):
        env, mgr = env_and_manager
        with pytest.raises(KeyError):
            mgr.restore_by_id("nonexistent-id")

    def test_undo(self, env_and_manager):
        env, mgr = env_and_manager
        env.write_file("x.txt", "before")
        mgr.create("snap")
        env.write_file("x.txt", "after")
        result = mgr.undo()
        assert result is not None
        assert result.name == "snap"
        assert env.read_file("x.txt") == "before"

    def test_undo_empty(self, env_and_manager):
        env, mgr = env_and_manager
        result = mgr.undo()
        assert result is None

    def test_get_by_name(self, env_and_manager):
        env, mgr = env_and_manager
        mgr.create("find-me")
        info = mgr.get("find-me")
        assert info is not None
        assert info.name == "find-me"

    def test_get_not_found(self, env_and_manager):
        env, mgr = env_and_manager
        assert mgr.get("nope") is None

    def test_clear(self, env_and_manager):
        env, mgr = env_and_manager
        mgr.create("a")
        mgr.create("b")
        mgr.clear()
        assert len(mgr.list_checkpoints()) == 0

    def test_time_str_format(self, env_and_manager):
        env, mgr = env_and_manager
        info = mgr.create("timed")
        assert ":" in info.time_str  # HH:MM:SS format

    def test_auto_checkpoint_property(self, env_and_manager):
        env, mgr = env_and_manager
        assert mgr.auto_checkpoint is False
        mgr.auto_checkpoint = True
        assert mgr.auto_checkpoint is True

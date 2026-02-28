"""Tests for multi-file edit transactions."""
from __future__ import annotations

import tempfile
from unittest.mock import patch

import pytest

from chimera.env.local import LocalEnvironment
from chimera.transactions import FileTransaction, StagedChange, TransactionState
from chimera.types import ChangeType, FileChange


def make_env(tmp_path_str: str) -> LocalEnvironment:
    env = LocalEnvironment(tmp_path_str)
    env.setup()
    return env


def test_stage_write_new_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        tx = FileTransaction(env)
        tx.stage_write("new.py", "print('hello')")
        change = tx._changes["new.py"]
        assert change.change_type == ChangeType.CREATE
        assert change.content == "print('hello')"
        assert change.original_content is None


def test_stage_write_existing_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        env.write_file("existing.py", "old content")
        tx = FileTransaction(env)
        tx.stage_write("existing.py", "new content")
        change = tx._changes["existing.py"]
        assert change.change_type == ChangeType.EDIT
        assert change.original_content == "old content"
        assert change.content == "new content"


def test_stage_delete() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        env.write_file("todelete.py", "some content")
        tx = FileTransaction(env)
        tx.stage_delete("todelete.py")
        change = tx._changes["todelete.py"]
        assert change.change_type == ChangeType.DELETE
        assert change.original_content == "some content"
        assert change.content is None


def test_stage_delete_missing_raises() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        tx = FileTransaction(env)
        with pytest.raises(FileNotFoundError):
            tx.stage_delete("nonexistent.py")


def test_preview_returns_file_changes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        env.write_file("a.py", "original")
        tx = FileTransaction(env)
        tx.stage_write("a.py", "modified")
        tx.stage_write("b.py", "brand new")
        changes = tx.preview()
        assert len(changes) == 2
        assert all(isinstance(c, FileChange) for c in changes)
        # Env should be unchanged after preview
        assert env.read_file("a.py") == "original"
        with pytest.raises(FileNotFoundError):
            env.read_file("b.py")


def test_commit_applies_changes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        tx = FileTransaction(env)
        tx.stage_write("result.py", "print('done')")
        tx.commit()
        assert env.read_file("result.py") == "print('done')"


def test_commit_returns_file_changes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        tx = FileTransaction(env)
        tx.stage_write("x.py", "content")
        result = tx.commit()
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], FileChange)


def test_rollback_restores_state() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        env.write_file("before.py", "original")
        tx = FileTransaction(env)
        tx.stage_write("before.py", "changed")
        tx.commit()
        assert env.read_file("before.py") == "changed"
        tx.rollback()
        assert env.read_file("before.py") == "original"


def test_rollback_without_commit_raises() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        tx = FileTransaction(env)
        tx.stage_write("file.py", "content")
        with pytest.raises(RuntimeError, match="Can only rollback a committed transaction"):
            tx.rollback()


def test_stage_after_commit_raises() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        tx = FileTransaction(env)
        tx.stage_write("file.py", "content")
        tx.commit()
        with pytest.raises(RuntimeError):
            tx.stage_write("another.py", "more")


def test_commit_empty_raises() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        tx = FileTransaction(env)
        with pytest.raises(RuntimeError, match="No changes staged"):
            tx.commit()


def test_commit_auto_rollback_on_failure() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        env.write_file("safe.py", "untouched")
        tx = FileTransaction(env)
        tx.stage_write("safe.py", "should not persist")
        tx.stage_write("other.py", "also should not persist")

        call_count = 0
        original_write = env.write_file

        def failing_write(path: str, content: str) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise IOError("Simulated write failure")
            original_write(path, content)

        with patch.object(env, "write_file", side_effect=failing_write):
            with pytest.raises(IOError):
                tx.commit()

        # State should be restored via checkpoint
        assert env.read_file("safe.py") == "untouched"


def test_context_manager_rollback() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        env.write_file("cm.py", "original")
        tx = FileTransaction(env)
        tx.stage_write("cm.py", "modified")

        try:
            with tx:
                tx.commit()
                assert env.read_file("cm.py") == "modified"
                raise ValueError("something went wrong")
        except ValueError:
            pass

        assert env.read_file("cm.py") == "original"
        assert tx._state == TransactionState.ROLLED_BACK


def test_multiple_writes_same_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        env = make_env(tmpdir)
        tx = FileTransaction(env)
        tx.stage_write("file.py", "first")
        tx.stage_write("file.py", "second")
        assert tx._changes["file.py"].content == "second"
        tx.commit()
        assert env.read_file("file.py") == "second"

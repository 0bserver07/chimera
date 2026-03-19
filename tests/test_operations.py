"""Tests for chimera.core.operations."""
import os
from chimera.core.operations import (
    ReadOps, WriteOps, BashOps, SearchOps,
    LocalReadOps, LocalWriteOps, LocalBashOps, LocalSearchOps,
)


def test_local_read_ops(tmp_path):
    (tmp_path / "hello.txt").write_text("hello world")
    ops = LocalReadOps(cwd=str(tmp_path))
    assert ops.read_file("hello.txt") == "hello world"


def test_local_read_ops_absolute(tmp_path):
    f = tmp_path / "abs.txt"
    f.write_text("absolute")
    ops = LocalReadOps(cwd="/tmp")
    assert ops.read_file(str(f)) == "absolute"


def test_local_read_ops_file_exists(tmp_path):
    (tmp_path / "exists.txt").write_text("yes")
    ops = LocalReadOps(cwd=str(tmp_path))
    assert ops.file_exists("exists.txt")
    assert not ops.file_exists("nope.txt")


def test_local_write_ops(tmp_path):
    ops = LocalWriteOps(cwd=str(tmp_path))
    ops.write_file("out.txt", "content")
    assert (tmp_path / "out.txt").read_text() == "content"


def test_local_write_ops_creates_dirs(tmp_path):
    ops = LocalWriteOps(cwd=str(tmp_path))
    ops.write_file("sub/dir/file.txt", "nested")
    assert (tmp_path / "sub" / "dir" / "file.txt").read_text() == "nested"


def test_local_bash_ops(tmp_path):
    ops = LocalBashOps(cwd=str(tmp_path))
    result = ops.run_command("echo hello")
    assert result.exit_code == 0
    assert "hello" in result.stdout


def test_local_bash_ops_timeout(tmp_path):
    ops = LocalBashOps(cwd=str(tmp_path))
    result = ops.run_command("sleep 10", timeout=1)
    assert result.exit_code != 0 or "Timeout" in result.stderr


def test_local_search_ops_list(tmp_path):
    (tmp_path / "a.py").write_text("def foo(): pass")
    (tmp_path / "b.txt").write_text("hello")
    ops = LocalSearchOps(cwd=str(tmp_path))
    files = ops.list_files("**/*")
    assert len(files) >= 2


def test_local_search_ops_search(tmp_path):
    (tmp_path / "a.py").write_text("def foo(): pass\ndef bar(): pass")
    ops = LocalSearchOps(cwd=str(tmp_path))
    results = ops.search_files("def foo")
    assert len(results) >= 1
    assert "foo" in results[0]


def test_protocol_compliance():
    assert isinstance(LocalReadOps(), ReadOps)
    assert isinstance(LocalWriteOps(), WriteOps)
    assert isinstance(LocalBashOps(), BashOps)
    assert isinstance(LocalSearchOps(), SearchOps)

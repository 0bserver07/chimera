"""Path-containment tests for :class:`LocalEnvironment` and ops backends.

Regression tests for the pre-0.2.0 security bugs:

* ``Path(workdir) / "/etc/passwd"`` discards the workdir (documented pathlib
  behavior), so ``read_file("/etc/passwd")`` read the real file.
* ``read_file("../../id_rsa")`` walked out of the workdir with no check.
* :py:meth:`LocalEnvironment.restore` ``shutil.rmtree``'s every entry under
  workdir, which is catastrophic when workdir is ``/`` or ``$HOME``.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from chimera.core.operations import LocalReadOps, LocalWriteOps
from chimera.env.local import LocalEnvironment


@pytest.fixture
def env(tmp_path):
    e = LocalEnvironment(workdir=str(tmp_path), test_cmd="python -m pytest")
    e.setup()
    yield e
    e.cleanup()


# ---- LocalEnvironment.read_file ------------------------------------------------


def test_read_file_absolute_path_outside_workdir_raises(env):
    with pytest.raises(PermissionError, match="escapes workdir"):
        env.read_file("/etc/passwd")


def test_read_file_dotdot_traversal_raises(env):
    with pytest.raises(PermissionError, match="escapes workdir"):
        env.read_file("../../id_rsa")


def test_read_file_absolute_path_inside_workdir_allowed(env):
    env.write_file("inside.txt", "ok")
    abs_path = str(env.workdir / "inside.txt")
    assert env.read_file(abs_path) == "ok"


# ---- LocalEnvironment.write_file -----------------------------------------------


def test_write_file_absolute_path_outside_workdir_raises(env, tmp_path):
    # Pick a target that definitely does not sit under workdir.
    target = tempfile.mkdtemp(prefix="outside-")
    try:
        with pytest.raises(PermissionError, match="escapes workdir"):
            env.write_file(os.path.join(target, "pwned.txt"), "nope")
    finally:
        # Confirm the write did not land.
        assert not (Path(target) / "pwned.txt").exists()
        os.rmdir(target)


def test_write_file_dotdot_traversal_raises(env):
    with pytest.raises(PermissionError, match="escapes workdir"):
        env.write_file("../escaped.txt", "nope")


# ---- Symlink attacks -----------------------------------------------------------


def test_symlink_inside_workdir_pointing_outside_raises(env, tmp_path):
    # Secret lives outside the sandbox.
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("top secret")

    # Symlink inside the sandbox points at it. ``resolve()`` follows the
    # symlink, then ``relative_to(workdir)`` catches the escape.
    link = env.workdir / "link"
    link.symlink_to(outside)

    try:
        with pytest.raises(PermissionError, match="escapes workdir"):
            env.read_file("link")
    finally:
        if outside.exists():
            outside.unlink()


# ---- restore() refusal ---------------------------------------------------------


def test_restore_refuses_when_workdir_is_home(monkeypatch, tmp_path):
    # Pretend ``$HOME`` is ``tmp_path`` so the safety check trips without
    # risking the real home directory.
    monkeypatch.setenv("HOME", str(tmp_path))
    # os.path.expanduser caches via ``HOME``; confirm the monkeypatch took.
    assert os.path.expanduser("~") == str(tmp_path)

    e = LocalEnvironment(workdir=str(tmp_path))
    e.setup()
    try:
        cp = e.checkpoint()
        with pytest.raises(PermissionError, match="root or HOME"):
            e.restore(cp)
    finally:
        e.cleanup()


def test_restore_refuses_when_workdir_is_root(monkeypatch):
    # Build a ``LocalEnvironment`` pointing at ``/`` without actually calling
    # ``setup`` (which would try to create ``/.chimera_checkpoints``).
    e = LocalEnvironment.__new__(LocalEnvironment)
    e.workdir = Path("/").resolve()
    e.test_cmd = "python -m pytest"
    e.timeout = 300
    e._use_session = False
    e._checkpoint_dir = e.workdir / ".chimera_checkpoints"

    # Fake the checkpoint existing so we reach the safety check.
    monkeypatch.setattr(Path, "exists", lambda self: True)

    with pytest.raises(PermissionError, match="root or HOME"):
        e.restore("0")


# ---- LocalReadOps / LocalWriteOps (chimera.core.operations) --------------------


def test_read_ops_absolute_path_outside_cwd_raises(tmp_path):
    ops = LocalReadOps(cwd=str(tmp_path))
    with pytest.raises(PermissionError, match="escapes sandbox cwd"):
        ops.read_file("/etc/passwd")


def test_read_ops_dotdot_traversal_raises(tmp_path):
    ops = LocalReadOps(cwd=str(tmp_path))
    with pytest.raises(PermissionError, match="escapes sandbox cwd"):
        ops.read_file("../../id_rsa")


def test_read_ops_file_exists_returns_false_for_escapes(tmp_path):
    # ``file_exists`` swallows the PermissionError and reports False so
    # callers treating it as a boolean probe do not leak existence info.
    ops = LocalReadOps(cwd=str(tmp_path))
    assert ops.file_exists("/etc/passwd") is False


def test_write_ops_absolute_path_outside_cwd_raises(tmp_path):
    outside = tempfile.mkdtemp(prefix="outside-")
    try:
        ops = LocalWriteOps(cwd=str(tmp_path))
        target = os.path.join(outside, "pwned.txt")
        with pytest.raises(PermissionError, match="escapes sandbox cwd"):
            ops.write_file(target, "nope")
        assert not Path(target).exists()
    finally:
        os.rmdir(outside)


def test_write_ops_dotdot_traversal_raises(tmp_path):
    ops = LocalWriteOps(cwd=str(tmp_path))
    with pytest.raises(PermissionError, match="escapes sandbox cwd"):
        ops.write_file("../escaped.txt", "nope")

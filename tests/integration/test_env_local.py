# tests/test_env_local.py
import tempfile

import pytest

from chimera.env.local import LocalEnvironment


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmpdir:
        e = LocalEnvironment(workdir=tmpdir, test_cmd="python -m pytest")
        e.setup()
        yield e
        e.cleanup()


def test_write_and_read_file(env):
    env.write_file("hello.txt", "world")
    assert env.read_file("hello.txt") == "world"


def test_read_nonexistent_file(env):
    with pytest.raises(FileNotFoundError):
        env.read_file("nope.txt")


def test_write_creates_subdirs(env):
    env.write_file("a/b/c.txt", "deep")
    assert env.read_file("a/b/c.txt") == "deep"


def test_list_files(env):
    env.write_file("a.py", "x")
    env.write_file("b.py", "y")
    env.write_file("sub/c.py", "z")
    files = env.list_files("**/*.py")
    assert len(files) == 3


def test_run_command(env):
    result = env.run_command("echo hello")
    assert result.success
    assert "hello" in result.stdout


def test_run_command_failure(env):
    result = env.run_command("false")
    assert not result.success


def test_checkpoint_and_restore(env):
    env.write_file("data.txt", "version1")
    cp = env.checkpoint()

    env.write_file("data.txt", "version2")
    assert env.read_file("data.txt") == "version2"

    env.restore(cp)
    assert env.read_file("data.txt") == "version1"


def test_run_tests_no_tests(env):
    result = env.run_tests()
    # With no test files, pytest exits with code 5 (no tests collected)
    assert result.total == 0

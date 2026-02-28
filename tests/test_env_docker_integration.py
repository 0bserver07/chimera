"""Integration tests for DockerEnvironment.

These tests require a running Docker daemon and are skipped otherwise.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from chimera.env.docker import DockerEnvironment


def _docker_available() -> bool:
    """Check whether Docker daemon is accessible."""
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=True,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


pytestmark = pytest.mark.skipif(
    not _docker_available(), reason="Docker not available"
)


@pytest.fixture
def docker_env():
    """Create and tear down a DockerEnvironment."""
    env = DockerEnvironment(
        image="python:3.11-slim",
        workdir="/workspace",
        test_cmd="python -m pytest",
    )
    env.setup()
    yield env
    env.cleanup()


class TestDockerIntegration:
    def test_setup_creates_container(self, docker_env):
        """Container should be running after setup."""
        assert docker_env._container is not None
        docker_env._container.reload()
        assert docker_env._container.status == "running"

    def test_run_command(self, docker_env):
        """Basic command execution should work."""
        result = docker_env.run_command("echo hello")
        assert result.stdout.strip() == "hello"
        assert result.exit_code == 0

    def test_run_command_exit_code(self, docker_env):
        """Non-zero exit codes should be captured."""
        result = docker_env.run_command("exit 42")
        assert result.exit_code == 42

    def test_write_and_read_file(self, docker_env):
        """Write a file and read it back."""
        content = "Hello, Docker!\nLine 2\n"
        docker_env.write_file("test.txt", content)
        result = docker_env.read_file("test.txt")
        assert result == content

    def test_write_nested_path(self, docker_env):
        """Writing to nested paths should create parent dirs."""
        content = "nested content"
        docker_env.write_file("a/b/c.txt", content)
        result = docker_env.read_file("a/b/c.txt")
        assert result == content

    def test_read_missing_file_raises(self, docker_env):
        """Reading a non-existent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            docker_env.read_file("nonexistent.txt")

    def test_list_files(self, docker_env):
        """List files should return written files."""
        docker_env.write_file("one.py", "# one")
        docker_env.write_file("two.py", "# two")
        docker_env.write_file("sub/three.py", "# three")
        files = docker_env.list_files()
        assert "one.py" in files
        assert "two.py" in files
        # sub/three.py might appear as sub/three.py
        assert any("three.py" in f for f in files)

    def test_run_tests(self, docker_env):
        """Write a simple test file and run tests."""
        # Install pytest first
        docker_env.run_command("pip install pytest -q")
        docker_env.write_file(
            "test_hello.py",
            "def test_pass():\n    assert True\n",
        )
        result = docker_env.run_tests()
        assert result.passed >= 1

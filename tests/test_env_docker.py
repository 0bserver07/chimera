"""Tests for DockerEnvironment (mocked -- no real Docker needed)."""
from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Build a mock 'docker' module so DockerEnvironment can be imported
# ---------------------------------------------------------------------------

def _make_mock_docker() -> ModuleType:
    mod = ModuleType("docker")
    mod.from_env = MagicMock  # type: ignore[attr-defined]
    return mod


@pytest.fixture(autouse=True)
def _inject_docker_module():
    """Ensure `import docker` succeeds with a mock."""
    mock_mod = _make_mock_docker()
    with patch.dict(sys.modules, {"docker": mock_mod}):
        yield


def _import_docker_env():
    """Import after mock injection (deferred to avoid top-level ImportError)."""
    # Force re-evaluation of the docker import guard
    import importlib
    import chimera.env.docker as mod
    importlib.reload(mod)
    return mod.DockerEnvironment


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDockerEnvironmentSetup:
    def test_setup_creates_container(self):
        DockerEnvironment = _import_docker_env()

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_client.containers.run.return_value = mock_container

        env = DockerEnvironment.__new__(DockerEnvironment)
        env._image = "python:3.11-slim"
        env._workdir = "/workspace"
        env._test_cmd = "python -m pytest"
        env._sandbox = None
        env._container = None
        env._client = None
        env._checkpoints = {}
        env._files = {}

        with patch("chimera.env.docker.docker") as mock_docker_mod:
            mock_docker_mod.from_env.return_value = mock_client
            env.setup()

        mock_client.containers.run.assert_called_once()
        assert env._container is mock_container

    def test_cleanup_stops_container(self):
        DockerEnvironment = _import_docker_env()

        mock_container = MagicMock()
        env = DockerEnvironment.__new__(DockerEnvironment)
        env._container = mock_container
        env._client = None
        env._checkpoints = {}
        env._files = {}

        env.cleanup()
        mock_container.stop.assert_called_once_with(timeout=5)

    def test_cleanup_no_container(self):
        DockerEnvironment = _import_docker_env()

        env = DockerEnvironment.__new__(DockerEnvironment)
        env._container = None
        env._client = None
        env._checkpoints = {}
        env._files = {}

        # Should not raise
        env.cleanup()


class TestDockerEnvironmentFiles:
    def test_write_and_read_file(self):
        DockerEnvironment = _import_docker_env()

        env = DockerEnvironment.__new__(DockerEnvironment)
        env._container = None
        env._client = None
        env._checkpoints = {}
        env._files = {}

        env.write_file("hello.py", "print('hi')")
        assert env.read_file("hello.py") == "print('hi')"

    def test_read_nonexistent_file(self):
        DockerEnvironment = _import_docker_env()

        env = DockerEnvironment.__new__(DockerEnvironment)
        env._container = None
        env._client = None
        env._checkpoints = {}
        env._files = {}

        with pytest.raises(FileNotFoundError):
            env.read_file("nope.py")

    def test_list_files(self):
        DockerEnvironment = _import_docker_env()

        env = DockerEnvironment.__new__(DockerEnvironment)
        env._container = None
        env._client = None
        env._checkpoints = {}
        env._files = {}

        env.write_file("a.py", "x")
        env.write_file("b.py", "y")
        files = env.list_files()
        assert sorted(files) == ["a.py", "b.py"]


class TestDockerEnvironmentCheckpoint:
    def test_checkpoint_and_restore(self):
        DockerEnvironment = _import_docker_env()

        env = DockerEnvironment.__new__(DockerEnvironment)
        env._container = None
        env._client = None
        env._checkpoints = {}
        env._files = {}

        env.write_file("data.txt", "v1")
        cp = env.checkpoint()

        env.write_file("data.txt", "v2")
        assert env.read_file("data.txt") == "v2"

        env.restore(cp)
        assert env.read_file("data.txt") == "v1"

    def test_restore_unknown_checkpoint(self):
        DockerEnvironment = _import_docker_env()

        env = DockerEnvironment.__new__(DockerEnvironment)
        env._container = None
        env._client = None
        env._checkpoints = {}
        env._files = {}

        with pytest.raises(ValueError, match="not found"):
            env.restore("nonexistent")

    def test_checkpoint_with_live_container_raises(self):
        """Live-container snapshotting is not implemented -- must fail loudly."""
        DockerEnvironment = _import_docker_env()

        env = DockerEnvironment.__new__(DockerEnvironment)
        env._container = MagicMock()  # pretend a live container is attached
        env._client = None
        env._checkpoints = {}
        env._files = {}

        with pytest.raises(NotImplementedError, match="live containers"):
            env.checkpoint()

    def test_restore_with_live_container_raises(self):
        """Live-container restore is not implemented -- must fail loudly."""
        DockerEnvironment = _import_docker_env()

        env = DockerEnvironment.__new__(DockerEnvironment)
        env._container = MagicMock()
        env._client = None
        env._checkpoints = {"cp1": {}}
        env._files = {}

        with pytest.raises(NotImplementedError, match="live containers"):
            env.restore("cp1")


class TestDockerEnvironmentImportGuard:
    def test_import_error_when_docker_missing(self):
        """If the docker package is not installed the constructor raises."""
        with patch.dict(sys.modules, {"docker": None}):
            import importlib
            import chimera.env.docker as mod
            importlib.reload(mod)
            # After reload, docker is None inside the module
            with patch.object(mod, "docker", None):
                with pytest.raises(ImportError, match="pip install docker"):
                    mod.DockerEnvironment()

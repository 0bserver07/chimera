"""Tests for RemoteEnvironment with mocked HTTP."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

from chimera.types import CommandResult, TestResult


@pytest.fixture()
def mock_httpx():
    """Provide a mocked httpx module and Client instance."""
    mock_client_instance = MagicMock()
    mock_client_cls = MagicMock(return_value=mock_client_instance)
    mock_module = MagicMock()
    mock_module.Client = mock_client_cls
    return mock_module, mock_client_cls, mock_client_instance


@pytest.fixture()
def env(mock_httpx):
    """Create a RemoteEnvironment with a mocked httpx client."""
    mock_module, mock_client_cls, mock_client_instance = mock_httpx
    with patch.dict(sys.modules, {"httpx": mock_module}):
        # Re-import to pick up the mock
        import importlib
        import chimera.env.remote as remote_mod
        importlib.reload(remote_mod)

        environment = remote_mod.RemoteEnvironment(
            host="example.com",
            port=9090,
            api_key="secret-key",
            working_dir="/workspace",
            timeout=60,
            tls=True,
        )
    # Replace internal client with our mock
    environment._client = mock_client_instance
    return environment, mock_client_instance


class TestSetup:
    def test_setup_calls_health(self, env):
        environment, client = env
        client.get.return_value = MagicMock(status_code=200)
        environment.setup()
        client.get.assert_called_once_with("/health")

    def test_setup_raises_on_http_error(self, env):
        environment, client = env
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("503 Service Unavailable")
        client.get.return_value = resp
        with pytest.raises(Exception, match="503"):
            environment.setup()


class TestRunCommand:
    def test_run_command(self, env):
        environment, client = env
        resp = MagicMock()
        resp.json.return_value = {"stdout": "hello", "stderr": "", "exit_code": 0}
        client.post.return_value = resp

        result = environment.run_command("echo hello", timeout=30, shell_name="main")

        assert isinstance(result, CommandResult)
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.exit_code == 0
        client.post.assert_called_once_with(
            "/execute",
            json={"cmd": "echo hello", "timeout": 30, "shell_name": "main"},
        )


class TestReadFile:
    def test_read_file(self, env):
        environment, client = env
        resp = MagicMock()
        resp.json.return_value = {"content": "file content"}
        client.get.return_value = resp

        content = environment.read_file("src/main.py")

        assert content == "file content"
        client.get.assert_called_once_with("/files/read", params={"path": "src/main.py"})


class TestWriteFile:
    def test_write_file(self, env):
        environment, client = env
        resp = MagicMock()
        client.post.return_value = resp

        environment.write_file("src/main.py", "print('hi')")

        client.post.assert_called_once_with(
            "/files/write",
            json={"path": "src/main.py", "content": "print('hi')"},
        )


class TestListFiles:
    def test_list_files(self, env):
        environment, client = env
        resp = MagicMock()
        resp.json.return_value = {"files": ["a.py", "b.py"]}
        client.get.return_value = resp

        files = environment.list_files("*.py")

        assert files == ["a.py", "b.py"]
        client.get.assert_called_once_with("/files/list", params={"pattern": "*.py"})

    def test_list_files_default_pattern(self, env):
        environment, client = env
        resp = MagicMock()
        resp.json.return_value = {"files": []}
        client.get.return_value = resp

        environment.list_files()

        client.get.assert_called_once_with("/files/list", params={"pattern": "**/*"})


class TestRunTests:
    def test_run_tests(self, env):
        environment, client = env
        resp = MagicMock()
        resp.json.return_value = {
            "passed": 10,
            "failed": 2,
            "errors": 1,
            "output": "test output",
        }
        client.post.return_value = resp

        result = environment.run_tests()

        assert isinstance(result, TestResult)
        assert result.passed == 10
        assert result.failed == 2
        assert result.errors == 1
        assert result.output == "test output"
        client.post.assert_called_once_with("/tests/run")


class TestCheckpointRestore:
    def test_checkpoint(self, env):
        environment, client = env
        resp = MagicMock()
        resp.json.return_value = {"checkpoint_id": "cp-123"}
        client.post.return_value = resp

        cp_id = environment.checkpoint()

        assert cp_id == "cp-123"
        client.post.assert_called_once_with("/checkpoint")

    def test_restore(self, env):
        environment, client = env
        resp = MagicMock()
        client.post.return_value = resp

        environment.restore("cp-123")

        client.post.assert_called_once_with(
            "/restore", json={"checkpoint_id": "cp-123"}
        )


class TestUploadDownload:
    def test_upload_file(self, env):
        environment, client = env
        resp = MagicMock()
        client.post.return_value = resp

        m = mock_open(read_data=b"binary data")
        with patch("builtins.open", m):
            environment.upload_file("/tmp/local.txt", "remote.txt")

        m.assert_called_once_with("/tmp/local.txt", "rb")
        assert client.post.call_count == 1
        call_args = client.post.call_args
        assert call_args[0][0] == "/files/upload"
        assert call_args[1]["data"] == {"path": "remote.txt"}

    def test_download_file(self, env):
        environment, client = env
        resp = MagicMock()
        resp.content = b"downloaded bytes"
        client.get.return_value = resp

        m = mock_open()
        with patch("builtins.open", m):
            environment.download_file("remote.txt", "/tmp/local.txt")

        client.get.assert_called_once_with(
            "/files/download", params={"path": "remote.txt"}
        )
        m.assert_called_once_with("/tmp/local.txt", "wb")
        m().write.assert_called_once_with(b"downloaded bytes")


class TestCleanup:
    def test_cleanup_closes_client(self, env):
        environment, client = env
        environment.cleanup()
        client.close.assert_called_once()


class TestTLS:
    def test_tls_url_construction(self, mock_httpx):
        mock_module, mock_client_cls, mock_client_instance = mock_httpx
        with patch.dict(sys.modules, {"httpx": mock_module}):
            import importlib
            import chimera.env.remote as remote_mod
            importlib.reload(remote_mod)

            env = remote_mod.RemoteEnvironment(
                host="secure.example.com",
                port=443,
                tls=True,
            )

        assert env._base_url == "https://secure.example.com:443"

    def test_http_url_construction(self, mock_httpx):
        """Plaintext HTTP is allowed for local dev without an api_key."""
        mock_module, mock_client_cls, mock_client_instance = mock_httpx
        with patch.dict(sys.modules, {"httpx": mock_module}):
            import importlib
            import chimera.env.remote as remote_mod
            importlib.reload(remote_mod)

            env = remote_mod.RemoteEnvironment(host="example.com", tls=False)

        assert env._base_url == "http://example.com:8080"

    def test_default_scheme_is_https(self, mock_httpx):
        """Default construction uses TLS to avoid accidental plaintext."""
        mock_module, mock_client_cls, mock_client_instance = mock_httpx
        with patch.dict(sys.modules, {"httpx": mock_module}):
            import importlib
            import chimera.env.remote as remote_mod
            importlib.reload(remote_mod)

            env = remote_mod.RemoteEnvironment(host="example.com")

        assert env._base_url == "https://example.com:8080"

    def test_api_key_without_tls_raises(self, mock_httpx):
        """api_key with tls=False must raise to prevent plaintext bearer leak."""
        mock_module, mock_client_cls, mock_client_instance = mock_httpx
        with patch.dict(sys.modules, {"httpx": mock_module}):
            import importlib
            import chimera.env.remote as remote_mod
            importlib.reload(remote_mod)

            with pytest.raises(ValueError, match="plaintext HTTP"):
                remote_mod.RemoteEnvironment(
                    host="example.com",
                    api_key="secret",
                    tls=False,
                )


class TestAPIKey:
    def test_api_key_in_headers(self, mock_httpx):
        mock_module, mock_client_cls, mock_client_instance = mock_httpx
        with patch.dict(sys.modules, {"httpx": mock_module}):
            import importlib
            import chimera.env.remote as remote_mod
            importlib.reload(remote_mod)

            remote_mod.RemoteEnvironment(
                host="example.com",
                api_key="my-key",
            )

        call_kwargs = mock_client_cls.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer my-key"

    def test_no_api_key_no_auth_header(self, mock_httpx):
        mock_module, mock_client_cls, mock_client_instance = mock_httpx
        with patch.dict(sys.modules, {"httpx": mock_module}):
            import importlib
            import chimera.env.remote as remote_mod
            importlib.reload(remote_mod)

            remote_mod.RemoteEnvironment(host="example.com")

        call_kwargs = mock_client_cls.call_args[1]
        assert "Authorization" not in call_kwargs["headers"]


class TestErrorHandling:
    def test_read_file_http_error(self, env):
        environment, client = env
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("404 Not Found")
        client.get.return_value = resp

        with pytest.raises(Exception, match="404"):
            environment.read_file("nonexistent.py")

    def test_run_command_http_error(self, env):
        environment, client = env
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("500 Internal Server Error")
        client.post.return_value = resp

        with pytest.raises(Exception, match="500"):
            environment.run_command("bad command")


class TestImportError:
    def test_import_error_message(self):
        with patch.dict(sys.modules, {"httpx": None}):
            import chimera.env.remote as remote_mod

            # Force the module-level httpx to be None
            original = remote_mod.httpx
            remote_mod.httpx = None  # type: ignore[assignment]
            try:
                with pytest.raises(ImportError, match="httpx is required"):
                    remote_mod.RemoteEnvironment(host="example.com")
            finally:
                remote_mod.httpx = original

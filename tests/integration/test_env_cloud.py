"""Tests for CloudEnvironment with mocked HTTP."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def mock_httpx():
    """Provide a mocked httpx module."""
    mock_client_instance = MagicMock()
    mock_client_cls = MagicMock(return_value=mock_client_instance)
    mock_module = MagicMock()
    mock_module.Client = mock_client_cls
    return mock_module, mock_client_cls, mock_client_instance


@pytest.fixture()
def cloud_env_factory(mock_httpx):
    """Factory that creates a CloudEnvironment with mocked httpx."""
    mock_module, mock_client_cls, mock_client_instance = mock_httpx

    def factory(**kwargs):
        defaults = {
            "cloud_api_url": "https://api.cloud.example.com",
            "cloud_api_key": "cloud-key",
            "image": "python:3.11-slim",
            "working_dir": "/workspace",
            "keep_alive": False,
            "init_timeout": 10,
        }
        defaults.update(kwargs)

        with patch.dict(sys.modules, {"httpx": mock_module}):
            import importlib
            import chimera.env.remote as remote_mod
            import chimera.env.cloud as cloud_mod
            importlib.reload(remote_mod)
            importlib.reload(cloud_mod)

            env = cloud_mod.CloudEnvironment(**defaults)

        # Replace internal cloud client with our mock
        env._cloud_client = mock_client_instance
        return env, mock_client_instance

    return factory


class TestSetupNewSandbox:
    def test_creates_sandbox_and_waits_for_ready(self, cloud_env_factory):
        env, client = cloud_env_factory()

        # POST /sandboxes returns sandbox_id
        create_resp = MagicMock()
        create_resp.json.return_value = {"sandbox_id": "sb-001"}

        # GET /sandboxes/sb-001 returns ready status
        status_resp = MagicMock()
        status_resp.json.return_value = {
            "status": "ready",
            "host": "sandbox.example.com",
            "port": 8080,
            "sandbox_id": "sb-001",
        }

        # Health check for parent setup()
        MagicMock()

        client.post.return_value = create_resp
        client.get.return_value = status_resp

        # Patch the parent's __init__ and setup to avoid real HTTP
        with patch.object(type(env).__bases__[0], "setup"):
            with patch.object(type(env).__bases__[0], "__init__", return_value=None):
                env.setup()

        assert env._sandbox_id == "sb-001"
        client.post.assert_called_once_with(
            "/sandboxes",
            json={"working_dir": "/workspace", "image": "python:3.11-slim"},
        )


class TestSetupExistingSandbox:
    def test_connects_to_existing_sandbox(self, cloud_env_factory):
        env, client = cloud_env_factory(sandbox_id="sb-existing")

        status_resp = MagicMock()
        status_resp.json.return_value = {
            "status": "ready",
            "host": "sandbox.example.com",
            "port": 8080,
            "sandbox_id": "sb-existing",
        }
        client.get.return_value = status_resp

        with patch.object(type(env).__bases__[0], "setup"):
            with patch.object(type(env).__bases__[0], "__init__", return_value=None):
                env.setup()

        assert env._sandbox_id == "sb-existing"
        # Should NOT call POST /sandboxes
        client.post.assert_not_called()
        # Should poll status
        client.get.assert_called_with("/sandboxes/sb-existing")


class TestCleanupDeletesSandbox:
    def test_deletes_sandbox_when_keep_alive_false(self, cloud_env_factory, mock_httpx):
        env, client = cloud_env_factory(keep_alive=False)
        env._sandbox_id = "sb-del"

        # Give env a mock _client for the parent cleanup
        env._client = MagicMock()

        env.cleanup()

        client.delete.assert_called_once_with("/sandboxes/sb-del")
        client.close.assert_called_once()

    def test_preserves_sandbox_when_keep_alive_true(self, cloud_env_factory):
        env, client = cloud_env_factory(keep_alive=True)
        env._sandbox_id = "sb-keep"

        env._client = MagicMock()

        env.cleanup()

        client.delete.assert_not_called()
        client.close.assert_called_once()


class TestTimeout:
    def test_timeout_error_when_sandbox_not_ready(self, cloud_env_factory):
        env, client = cloud_env_factory(init_timeout=0)
        env._sandbox_id = "sb-slow"

        status_resp = MagicMock()
        status_resp.json.return_value = {"status": "provisioning"}
        client.get.return_value = status_resp

        with patch("time.sleep"):
            with pytest.raises(TimeoutError, match="not ready"):
                env.setup()


class TestSandboxError:
    def test_error_status_raises_runtime_error(self, cloud_env_factory):
        env, client = cloud_env_factory()
        env._sandbox_id = "sb-err"

        status_resp = MagicMock()
        status_resp.json.return_value = {"status": "error"}
        client.get.return_value = status_resp

        with pytest.raises(RuntimeError, match="error state"):
            env.setup()


class TestSandboxIdProperty:
    def test_sandbox_id_none_before_setup(self, cloud_env_factory):
        env, _ = cloud_env_factory()
        assert env.sandbox_id is None

    def test_sandbox_id_set_after_setup(self, cloud_env_factory):
        env, client = cloud_env_factory()

        create_resp = MagicMock()
        create_resp.json.return_value = {"sandbox_id": "sb-prop"}
        client.post.return_value = create_resp

        status_resp = MagicMock()
        status_resp.json.return_value = {
            "status": "ready",
            "host": "sandbox.example.com",
            "port": 8080,
        }
        client.get.return_value = status_resp

        with patch.object(type(env).__bases__[0], "setup"):
            with patch.object(type(env).__bases__[0], "__init__", return_value=None):
                env.setup()

        assert env.sandbox_id == "sb-prop"

    def test_sandbox_id_with_existing(self, cloud_env_factory):
        env, _ = cloud_env_factory(sandbox_id="sb-pre")
        assert env.sandbox_id == "sb-pre"

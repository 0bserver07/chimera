"""Tests for chimera.auth.manager (Issue #124)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.auth.manager import AuthManager


class TestLoadFromEnv:
    def test_load_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """AuthManager picks up ANTHROPIC_API_KEY from the environment."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-12345678")
        mgr = AuthManager(config_dir=tmp_path)
        cred = mgr._credentials.get("anthropic")
        assert cred is not None
        assert cred.provider == "anthropic"
        assert cred.key == "sk-ant-test-key-12345678"
        assert cred.source == "env"

    def test_load_from_env_prefers_first_var(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When both ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN are set, the first wins."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "first-key-00000000")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "second-key-0000000")
        mgr = AuthManager(config_dir=tmp_path)
        assert mgr._credentials["anthropic"].key == "first-key-00000000"


class TestGetToken:
    def test_get_token_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """get_token returns the env-loaded key."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test1234")
        mgr = AuthManager(config_dir=tmp_path)
        assert mgr.get_token("openai") == "sk-openai-test1234"

    def test_get_token_returns_none_when_missing(self, tmp_path: Path) -> None:
        """get_token returns None for an unconfigured provider."""
        mgr = AuthManager(config_dir=tmp_path)
        assert mgr.get_token("nonexistent") is None

    def test_get_token_from_config_file(self, tmp_path: Path) -> None:
        """get_token falls back to reading auth.json."""
        config = tmp_path / "auth.json"
        config.write_text(json.dumps({"anthropic": {"api_key": "from-config-key"}}))
        mgr = AuthManager(config_dir=tmp_path)
        assert mgr.get_token("anthropic") == "from-config-key"


class TestSetAndGetToken:
    def test_set_and_get_token(self, tmp_path: Path) -> None:
        """set_token persists a key and get_token retrieves it."""
        mgr = AuthManager(config_dir=tmp_path)
        mgr.set_token("anthropic", "sk-ant-new-key-1234")
        assert mgr.get_token("anthropic") == "sk-ant-new-key-1234"

        # Verify it was persisted to disk
        config = tmp_path / "auth.json"
        assert config.exists()
        data = json.loads(config.read_text())
        assert data["anthropic"]["api_key"] == "sk-ant-new-key-1234"

    def test_set_token_creates_config_dir(self, tmp_path: Path) -> None:
        """set_token creates the config directory if it doesn't exist."""
        nested = tmp_path / "deep" / "nested"
        mgr = AuthManager(config_dir=nested)
        mgr.set_token("openai", "sk-openai-key-12345")
        assert (nested / "auth.json").exists()


class TestRemoveToken:
    def test_remove_token(self, tmp_path: Path) -> None:
        """remove_token deletes from in-memory cache and disk."""
        mgr = AuthManager(config_dir=tmp_path)
        mgr.set_token("anthropic", "sk-to-remove-12345")
        assert mgr.remove_token("anthropic") is True
        assert mgr.get_token("anthropic") is None

    def test_remove_token_missing_returns_false(self, tmp_path: Path) -> None:
        """remove_token returns False when provider was never stored on disk."""
        mgr = AuthManager(config_dir=tmp_path)
        assert mgr.remove_token("nonexistent") is False


class TestListProviders:
    def test_list_providers(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """list_providers returns info about all configured providers."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key-123456789")
        mgr = AuthManager(config_dir=tmp_path)
        mgr.set_token("openai", "sk-openai-key-12345678")

        providers = mgr.list_providers()
        names = [p["provider"] for p in providers]
        assert "anthropic" in names
        assert "openai" in names
        for p in providers:
            assert "key_preview" in p
            assert p["key_preview"].endswith("...")
            assert "source" in p

    def test_list_providers_empty(self, tmp_path: Path) -> None:
        """list_providers returns empty list when nothing is configured."""
        mgr = AuthManager(config_dir=tmp_path)
        assert mgr.list_providers() == []


class TestStatus:
    def test_status_no_keys(self, tmp_path: Path) -> None:
        """status() returns guidance when no keys are configured."""
        mgr = AuthManager(config_dir=tmp_path)
        result = mgr.status()
        assert "No API keys configured" in result

    def test_status_with_keys(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """status() lists configured providers."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key-123456789")
        mgr = AuthManager(config_dir=tmp_path)
        result = mgr.status()
        assert "anthropic" in result
        assert "sk-ant-k..." in result
        assert "env" in result

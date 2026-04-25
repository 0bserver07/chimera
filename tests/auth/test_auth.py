# tests/test_auth.py
from __future__ import annotations

import os
import stat
import time

import pytest

from chimera.auth import (
    APIKeyAuth,
    AuthManager,
    AuthProvider,
    Credential,
    CredentialStore,
    OAuthBrowserFlow,
    OAuthDeviceFlow,
)


# ---------------------------------------------------------------------------
# Credential dataclass
# ---------------------------------------------------------------------------


class TestCredential:
    def test_basic_fields(self) -> None:
        cred = Credential(provider="anthropic", token="sk-abc")
        assert cred.provider == "anthropic"
        assert cred.token == "sk-abc"
        assert cred.refresh_token is None
        assert cred.expires_at is None
        assert cred.metadata == {}

    def test_is_expired_none_expiry(self) -> None:
        cred = Credential(provider="openai", token="tok")
        assert cred.is_expired is False

    def test_is_expired_future(self) -> None:
        cred = Credential(
            provider="openai",
            token="tok",
            expires_at=time.time() + 3600,
        )
        assert cred.is_expired is False

    def test_is_expired_past(self) -> None:
        cred = Credential(
            provider="openai",
            token="tok",
            expires_at=time.time() - 10,
        )
        assert cred.is_expired is True

    def test_metadata_default_is_independent(self) -> None:
        a = Credential(provider="a", token="1")
        b = Credential(provider="b", token="2")
        a.metadata["x"] = 1
        assert "x" not in b.metadata


# ---------------------------------------------------------------------------
# AuthProvider ABC
# ---------------------------------------------------------------------------


class TestAuthProviderABC:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            AuthProvider()  # type: ignore[abstract]

    def test_subclass_must_implement_methods(self) -> None:
        class Incomplete(AuthProvider):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# APIKeyAuth
# ---------------------------------------------------------------------------


class TestAPIKeyAuth:
    def test_explicit_key(self) -> None:
        auth = APIKeyAuth("anthropic", key="sk-explicit")
        cred = auth.authenticate()
        assert cred.token == "sk-explicit"
        assert cred.provider == "anthropic"

    def test_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_CUSTOM_KEY", "sk-env")
        auth = APIKeyAuth("custom", env_var="MY_CUSTOM_KEY")
        cred = auth.authenticate()
        assert cred.token == "sk-env"

    def test_common_env_var_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-common")
        # No explicit key or env_var; falls back to common mapping.
        auth = APIKeyAuth("anthropic")
        cred = auth.authenticate()
        assert cred.token == "sk-common"

    def test_raises_when_no_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        auth = APIKeyAuth("anthropic")
        with pytest.raises(ValueError, match="No API key found"):
            auth.authenticate()

    def test_refresh_returns_same_credential(self) -> None:
        auth = APIKeyAuth("openai", key="sk-key")
        cred = Credential(provider="openai", token="sk-key")
        assert auth.refresh(cred) is cred

    def test_provider_name_property(self) -> None:
        auth = APIKeyAuth("google")
        assert auth.provider_name == "google"


# ---------------------------------------------------------------------------
# OAuth flows (stdlib implementation — no httpx required)
# ---------------------------------------------------------------------------


class TestOAuthDeviceFlow:
    def test_provider_name(self) -> None:
        flow = OAuthDeviceFlow(
            provider_name="mycloud",
            client_id="cid",
            device_auth_url="https://example.com/device",
            token_url="https://example.com/token",
        )
        assert flow.provider_name == "mycloud"

    def test_refresh_no_token_raises(self) -> None:
        flow = OAuthDeviceFlow(
            provider_name="test",
            client_id="cid",
            device_auth_url="https://example.com/device",
            token_url="https://example.com/token",
        )
        cred = Credential(provider="test", token="tok", refresh_token=None)
        with pytest.raises(ValueError, match="No refresh token"):
            flow.refresh(cred)


class TestOAuthBrowserFlow:
    def test_provider_name(self) -> None:
        flow = OAuthBrowserFlow(
            provider_name="mycloud",
            client_id="cid",
            auth_url="https://example.com/auth",
            token_url="https://example.com/token",
        )
        assert flow.provider_name == "mycloud"

    def test_refresh_no_token_raises(self) -> None:
        flow = OAuthBrowserFlow(
            provider_name="test",
            client_id="cid",
            auth_url="https://example.com/auth",
            token_url="https://example.com/token",
        )
        cred = Credential(provider="test", token="tok", refresh_token=None)
        with pytest.raises(ValueError, match="No refresh token"):
            flow.refresh(cred)


# ---------------------------------------------------------------------------
# CredentialStore
# ---------------------------------------------------------------------------


class TestCredentialStore:
    def test_save_and_get(self, tmp_path: str) -> None:
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        cred = Credential(
            provider="anthropic",
            token="sk-abc",
            metadata={"org": "acme"},
        )
        store.save(cred)

        loaded = store.get("anthropic")
        assert loaded is not None
        assert loaded.provider == "anthropic"
        assert loaded.token == "sk-abc"
        assert loaded.metadata == {"org": "acme"}

    def test_get_missing_returns_none(self, tmp_path: str) -> None:
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        assert store.get("nonexistent") is None

    def test_delete(self, tmp_path: str) -> None:
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        store.save(Credential(provider="openai", token="tok"))
        store.delete("openai")
        assert store.get("openai") is None

    def test_delete_missing_does_not_raise(self, tmp_path: str) -> None:
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        store.delete("nonexistent")  # should not raise

    def test_list_providers(self, tmp_path: str) -> None:
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        store.save(Credential(provider="a", token="1"))
        store.save(Credential(provider="b", token="2"))
        providers = store.list_providers()
        assert sorted(providers) == ["a", "b"]

    def test_list_providers_empty(self, tmp_path: str) -> None:
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        assert store.list_providers() == []

    def test_file_permissions(self, tmp_path: str) -> None:
        cred_path = f"{tmp_path}/creds.json"
        store = CredentialStore(path=cred_path)
        store.save(Credential(provider="test", token="tok"))

        file_stat = os.stat(cred_path)
        mode = stat.S_IMODE(file_stat.st_mode)
        assert mode == 0o600

    def test_handles_missing_file(self, tmp_path: str) -> None:
        store = CredentialStore(path=f"{tmp_path}/does/not/exist.json")
        assert store.get("any") is None
        assert store.list_providers() == []

    def test_save_creates_parent_directories(self, tmp_path: str) -> None:
        cred_path = f"{tmp_path}/deep/nested/dir/creds.json"
        store = CredentialStore(path=cred_path)
        store.save(Credential(provider="p", token="t"))
        assert store.get("p") is not None

    def test_overwrite_existing_provider(self, tmp_path: str) -> None:
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        store.save(Credential(provider="x", token="old"))
        store.save(Credential(provider="x", token="new"))
        loaded = store.get("x")
        assert loaded is not None
        assert loaded.token == "new"


# ---------------------------------------------------------------------------
# AuthManager
# ---------------------------------------------------------------------------


class TestAuthManager:
    def test_login_with_api_key(
        self,
        tmp_path: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        mgr = AuthManager(store=store)
        cred = mgr.login("anthropic")
        assert cred.token == "sk-test"

    def test_login_caches_credential(
        self,
        tmp_path: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        mgr = AuthManager(store=store)
        cred1 = mgr.login("anthropic")
        # Second call should return the cached credential from the store.
        cred2 = mgr.login("anthropic")
        assert cred1.token == cred2.token

    def test_get_token_with_cached_credential(
        self,
        tmp_path: str,
    ) -> None:
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        store.save(Credential(provider="openai", token="sk-cached"))
        mgr = AuthManager(store=store)
        assert mgr.get_token("openai") == "sk-cached"

    def test_logout(
        self,
        tmp_path: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-key")
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        mgr = AuthManager(store=store)
        mgr.login("openai")
        mgr.logout("openai")
        assert store.get("openai") is None

    def test_register_custom_provider(self, tmp_path: str) -> None:
        class CustomAuth(AuthProvider):
            @property
            def provider_name(self) -> str:
                return "custom"

            def authenticate(self) -> Credential:
                return Credential(
                    provider="custom",
                    token="custom-token",
                )

            def refresh(self, credential: Credential) -> Credential:
                return credential

        store = CredentialStore(path=f"{tmp_path}/creds.json")
        mgr = AuthManager(store=store)
        mgr.register(CustomAuth())
        cred = mgr.login("custom")
        assert cred.token == "custom-token"

    def test_login_unknown_method_raises(self, tmp_path: str) -> None:
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        mgr = AuthManager(store=store)
        with pytest.raises(ValueError, match="No auth provider"):
            mgr.login("anthropic", method="oauth")

    def test_get_token_triggers_login(
        self,
        tmp_path: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-auto")
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        mgr = AuthManager(store=store)
        # No prior login; get_token should auto-login.
        assert mgr.get_token("anthropic") == "sk-auto"

    def test_get_token_refreshes_expired(self, tmp_path: str) -> None:
        store = CredentialStore(path=f"{tmp_path}/creds.json")
        expired_cred = Credential(
            provider="custom",
            token="old-tok",
            expires_at=time.time() - 10,
        )
        store.save(expired_cred)

        class RefreshAuth(AuthProvider):
            @property
            def provider_name(self) -> str:
                return "custom"

            def authenticate(self) -> Credential:
                return Credential(provider="custom", token="new-tok")

            def refresh(self, credential: Credential) -> Credential:
                return Credential(
                    provider="custom",
                    token="refreshed-tok",
                    expires_at=time.time() + 3600,
                )

        mgr = AuthManager(store=store)
        mgr.register(RefreshAuth())
        token = mgr.get_token("custom")
        assert token == "refreshed-tok"

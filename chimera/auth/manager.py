"""Authentication management — backwards-compatible with new capabilities.

Preserves the original AuthManager API (store=, login(), register()) while
adding the new centralized key management (config_dir, get_token/set_token,
env var loading, auth.json persistence).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from chimera.auth.api_key import APIKeyAuth
from chimera.auth.base import AuthProvider, Credential
from chimera.auth.store import CredentialStore
from chimera.config.paths import chimera_home

__all__ = ["AuthManager", "StoredCredential"]


@dataclass
class StoredCredential:
    """A stored API credential with source tracking."""
    provider: str
    key: str
    source: str  # "env", "keyring", "config"


class AuthManager:
    """Facade for authentication. Manages credential lifecycle.

    Supports both the original API (store-based login/logout) and the
    new centralized key management (env vars, config file, get_token/set_token).
    """

    ENV_VARS = {
        "anthropic": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
        "openai": ["OPENAI_API_KEY"],
        "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "ollama": [],
    }

    def __init__(
        self,
        store: CredentialStore | None = None,
        config_dir: Path | None = None,
    ) -> None:
        # Original API
        self._store = store or CredentialStore()
        self._providers: dict[str, AuthProvider] = {}

        # New API
        self._config_dir = config_dir or chimera_home()
        self._credentials: dict[str, StoredCredential] = {}
        self._load_from_env()

    # ---- Original API (backwards compatible) ----

    def register(self, auth_provider: AuthProvider) -> None:
        """Register a custom :class:`AuthProvider`."""
        self._providers[auth_provider.provider_name] = auth_provider

    def login(
        self,
        provider: str = "anthropic",
        method: str = "api_key",
    ) -> Credential:
        """Authenticate and cache a credential."""
        existing = self._store.get(provider)
        if existing and not existing.is_expired:
            return existing

        if provider in self._providers:
            auth = self._providers[provider]
        elif method == "api_key":
            auth = APIKeyAuth(provider)
        else:
            raise ValueError(f"No auth provider for '{provider}' with method '{method}'")

        cred = auth.authenticate()
        self._store.save(cred)
        return cred

    def get(self, provider: str) -> Credential | None:
        """Retrieve a cached credential (original API)."""
        return self._store.get(provider)

    def logout(self, provider: str = "anthropic") -> None:
        """Remove cached credential (original API)."""
        self._store.delete(provider)

    # ---- New API (centralized key management) ----

    def _load_from_env(self) -> None:
        """Load credentials from environment variables."""
        for provider, env_vars in self.ENV_VARS.items():
            for var in env_vars:
                val = os.environ.get(var)
                if val:
                    self._credentials[provider] = StoredCredential(
                        provider=provider, key=val, source="env",
                    )
                    break

    def get_token(self, provider: str) -> str | None:
        """Get API token for a provider (new API)."""
        cred = self._credentials.get(provider)
        if cred:
            return cred.key
        # Try config file
        config_path = self._config_dir / "auth.json"
        if config_path.exists():
            data = json.loads(config_path.read_text())
            key: str | None = data.get(provider, {}).get("api_key")
            if key:
                self._credentials[provider] = StoredCredential(
                    provider=provider, key=key, source="config",
                )
                return key
        # Fall back to original store
        old_cred = self._store.get(provider)
        if old_cred and not old_cred.is_expired:
            return old_cred.token
        # Try refresh via registered providers (for expired tokens)
        if old_cred and old_cred.is_expired and provider in self._providers:
            auth = self._providers[provider]
            if hasattr(auth, 'refresh'):
                try:
                    refreshed = auth.refresh(old_cred)
                    self._store.save(refreshed)
                    return refreshed.token
                except Exception:
                    pass
        # Try login via registered providers (no token at all)
        if provider in self._providers:
            try:
                refreshed = self.login(provider)
                return refreshed.token
            except Exception:
                pass
        return None

    def set_token(self, provider: str, key: str) -> None:
        """Store API token (new API)."""
        self._credentials[provider] = StoredCredential(
            provider=provider, key=key, source="config",
        )
        config_path = self._config_dir / "auth.json"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        data = {}
        if config_path.exists():
            data = json.loads(config_path.read_text())
        data[provider] = {"api_key": key}
        config_path.write_text(json.dumps(data, indent=2))

    def remove_token(self, provider: str) -> bool:
        """Remove stored token (new API)."""
        removed = False
        if provider in self._credentials:
            del self._credentials[provider]
            removed = True
        config_path = self._config_dir / "auth.json"
        if config_path.exists():
            data = json.loads(config_path.read_text())
            if provider in data:
                del data[provider]
                config_path.write_text(json.dumps(data, indent=2))
                removed = True
        return removed

    def list_providers(self) -> list[dict[str, str]]:
        """List configured providers with source info."""
        result = []
        for provider, cred in self._credentials.items():
            result.append({
                "provider": provider,
                "source": cred.source,
                "key_preview": cred.key[:8] + "...",
            })
        return result

    def status(self) -> str:
        """Human-readable auth status."""
        providers = self.list_providers()
        if not providers:
            return "No API keys configured. Set ANTHROPIC_API_KEY or use 'chimera auth login'."
        lines = ["Configured providers:"]
        for p in providers:
            lines.append(f"  {p['provider']}: {p['key_preview']} (from {p['source']})")
        return "\n".join(lines)

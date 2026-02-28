from __future__ import annotations

from chimera.auth.api_key import APIKeyAuth
from chimera.auth.base import AuthProvider, Credential
from chimera.auth.store import CredentialStore

__all__ = ["AuthManager"]


class AuthManager:
    """Facade for authentication.  Manages credential lifecycle."""

    def __init__(self, store: CredentialStore | None = None) -> None:
        self._store = store or CredentialStore()
        self._providers: dict[str, AuthProvider] = {}

    def register(self, auth_provider: AuthProvider) -> None:
        """Register a custom :class:`AuthProvider`."""
        self._providers[auth_provider.provider_name] = auth_provider

    def login(
        self,
        provider: str = "anthropic",
        method: str = "api_key",
    ) -> Credential:
        """Authenticate and cache a credential.

        If a non-expired credential already exists in the store it is
        returned immediately.
        """
        existing = self._store.get(provider)
        if existing and not existing.is_expired:
            return existing

        auth = self._providers.get(provider)
        if auth is None:
            if method == "api_key":
                auth = APIKeyAuth(provider)
            else:
                raise ValueError(
                    f"No auth provider for {provider} with method {method}"
                )

        credential = auth.authenticate()
        self._store.save(credential)
        return credential

    def get_token(self, provider: str) -> str:
        """Return a valid token, refreshing or re-authenticating as needed."""
        credential = self._store.get(provider)
        if credential is None:
            credential = self.login(provider)
        if credential.is_expired:
            auth = self._providers.get(provider)
            if auth:
                credential = auth.refresh(credential)
                self._store.save(credential)
            else:
                credential = self.login(provider)
        return credential.token

    def logout(self, provider: str) -> None:
        """Remove stored credentials for *provider*."""
        self._store.delete(provider)

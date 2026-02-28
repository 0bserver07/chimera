from __future__ import annotations

import os

from chimera.auth.base import AuthProvider, Credential

__all__ = ["APIKeyAuth"]

# Well-known environment variables for popular providers.
_COMMON_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}


class APIKeyAuth(AuthProvider):
    """Authenticate via a static API key (env var or explicit value)."""

    def __init__(
        self,
        provider_name: str,
        env_var: str | None = None,
        key: str | None = None,
    ) -> None:
        self._provider_name = provider_name
        self._env_var = env_var
        self._key = key

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def authenticate(self) -> Credential:
        token = self._key

        # Try caller-specified env var.
        if token is None and self._env_var:
            token = os.environ.get(self._env_var)

        # Fall back to well-known env var for the provider.
        if token is None:
            env_var = _COMMON_ENV_VARS.get(self._provider_name)
            if env_var:
                token = os.environ.get(env_var)

        if token is None:
            raise ValueError(
                f"No API key found for {self._provider_name}"
            )

        return Credential(provider=self._provider_name, token=token)

    def refresh(self, credential: Credential) -> Credential:
        # API keys do not expire or refresh; return as-is.
        return credential

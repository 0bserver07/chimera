from __future__ import annotations

from chimera.auth.base import AuthProvider, Credential

__all__ = ["OAuthBrowserFlow", "OAuthDeviceFlow"]


class OAuthDeviceFlow(AuthProvider):
    """OAuth 2.0 device authorization grant flow (RFC 8628).

    Shows a code in the terminal; the user visits a URL to authorize.

    Note: actual HTTP calls require ``httpx`` which is an optional
    dependency.  Install with ``pip install chimera-ai[auth]``.
    """

    def __init__(
        self,
        provider_name: str,
        client_id: str,
        device_auth_url: str,
        token_url: str,
        poll_interval: int = 5,
        timeout: int = 300,
    ) -> None:
        self._provider_name = provider_name
        self._client_id = client_id
        self._device_auth_url = device_auth_url
        self._token_url = token_url
        self._poll_interval = poll_interval
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def authenticate(self) -> Credential:
        raise NotImplementedError(
            "OAuth device flow requires httpx. "
            "Install with: pip install chimera-ai[auth]"
        )

    def refresh(self, credential: Credential) -> Credential:  # noqa: ARG002
        raise NotImplementedError("OAuth refresh requires httpx")


class OAuthBrowserFlow(AuthProvider):
    """OAuth 2.0 authorization code flow with PKCE and local redirect.

    Note: actual HTTP calls require ``httpx`` which is an optional
    dependency.  Install with ``pip install chimera-ai[auth]``.
    """

    def __init__(
        self,
        provider_name: str,
        client_id: str,
        auth_url: str,
        token_url: str,
        redirect_port: int = 19876,
    ) -> None:
        self._provider_name = provider_name
        self._client_id = client_id
        self._auth_url = auth_url
        self._token_url = token_url
        self._redirect_port = redirect_port

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def authenticate(self) -> Credential:
        raise NotImplementedError(
            "OAuth browser flow requires httpx. "
            "Install with: pip install chimera-ai[auth]"
        )

    def refresh(self, credential: Credential) -> Credential:  # noqa: ARG002
        raise NotImplementedError("OAuth refresh requires httpx")

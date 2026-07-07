"""T3.7 — a provider AND its auth flow register out-of-tree and compose.

Proves the extension point is real: a third party (e.g. a plugin) can register
both a custom provider factory (providers.registry) and a custom auth flow
(AuthManager.register(AuthProvider)), and the two compose — the auth flow
resolves the token that builds the provider, with no framework code changed.
"""

from __future__ import annotations

from pathlib import Path

from chimera.auth.base import AuthProvider, Credential
from chimera.auth.manager import AuthManager
from chimera.auth.store import CredentialStore
from chimera.providers.registry import (
    get_provider_factory,
    register_provider,
    unregister_provider,
)


class _AcmeAuth(AuthProvider):
    """An out-of-tree auth flow — stands in for a plugin's custom OAuth."""

    def authenticate(self) -> Credential:
        return Credential(provider="acme", token="sk-acme-live-123")

    def refresh(self, credential: Credential) -> Credential:
        return Credential(provider="acme", token="sk-acme-refreshed")

    @property
    def provider_name(self) -> str:
        return "acme"


def _mgr(tmp_path: Path) -> AuthManager:
    return AuthManager(
        store=CredentialStore(path=str(tmp_path / "creds.json")),
        config_dir=tmp_path,
    )


def test_registered_auth_flow_resolves_token(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    mgr.register(_AcmeAuth())
    # No stored token → get_token drives the registered flow's authenticate().
    assert mgr.get_token("acme") == "sk-acme-live-123"


def test_provider_and_auth_compose_out_of_tree(tmp_path: Path) -> None:
    class _AcmeProvider:
        model_name = "acme-1"

        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

    register_provider(
        "acme",
        lambda model="", api_key=None, **kw: _AcmeProvider(api_key=api_key or ""),
    )
    try:
        mgr = _mgr(tmp_path)
        mgr.register(_AcmeAuth())

        token = mgr.get_token("acme")           # from the registered auth flow
        factory = get_provider_factory("acme")  # from the registered provider
        assert factory is not None
        provider = factory(model="acme-1", api_key=token)
        assert provider.api_key == "sk-acme-live-123"  # they compose
    finally:
        unregister_provider("acme")

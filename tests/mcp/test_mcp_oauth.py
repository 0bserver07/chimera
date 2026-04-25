"""Tests for OAuth2 PKCE flow + token store."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import urllib.request


from chimera.mcp import oauth as oauth_mod
from chimera.mcp.oauth import (
    OAuthClient,
    OAuthConfig,
    TokenStore,
    build_authorize_url,
    discover_metadata,
    generate_pkce_pair,
    oauth_config_from_dict,
)


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def test_pkce_pair_is_s256_hash_of_verifier():
    verifier, challenge = generate_pkce_pair()
    # Verifier is RFC 7636 compliant: 43-128 unreserved chars.
    assert 43 <= len(verifier) <= 128
    assert all(c.isalnum() or c in "-._~" for c in verifier)
    # Challenge MUST equal urlsafe_b64(SHA256(verifier)) without padding.
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    assert challenge == expected


def test_pkce_pairs_are_unique():
    pairs = {generate_pkce_pair() for _ in range(20)}
    assert len(pairs) == 20


def test_build_authorize_url_includes_pkce_params():
    url = build_authorize_url(
        auth_endpoint="https://issuer.example/authorize",
        client_id="cid",
        redirect_uri="http://127.0.0.1:7777/callback",
        code_challenge="abc123",
        scopes=["read", "write"],
        state="xyz",
    )
    assert url.startswith("https://issuer.example/authorize?")
    assert "code_challenge=abc123" in url
    assert "code_challenge_method=S256" in url
    assert "client_id=cid" in url
    assert "scope=read+write" in url
    assert "state=xyz" in url


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _fake_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")

    class _R:
        def __init__(self) -> None:
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._body

    return _R()


def test_discover_metadata_appends_well_known(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        return _fake_response({
            "authorization_endpoint": "https://issuer.example/authorize",
            "token_endpoint": "https://issuer.example/token",
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    meta = discover_metadata("https://issuer.example")
    assert captured["url"] == (
        "https://issuer.example/.well-known/oauth-authorization-server"
    )
    assert meta["token_endpoint"] == "https://issuer.example/token"


# ---------------------------------------------------------------------------
# Token store (file mode)
# ---------------------------------------------------------------------------

def test_token_store_writes_file_with_0o600(tmp_path):
    store = TokenStore(base_dir=tmp_path, prefer_keychain=False)
    store.save("server-a", {"access_token": "tok1", "refresh_token": "rt1"})
    expected = tmp_path / "server-a.json"
    assert expected.exists()
    # File must be 0o600.
    mode = stat.S_IMODE(os.stat(expected).st_mode)
    assert mode == 0o600
    # Round-trip.
    loaded = store.load("server-a")
    assert loaded == {"access_token": "tok1", "refresh_token": "rt1"}


def test_token_store_sanitizes_server_name(tmp_path):
    store = TokenStore(base_dir=tmp_path, prefer_keychain=False)
    store.save("https://weird/host:8080", {"access_token": "tok"})
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    # No path-traversal characters in the filename.
    name = files[0].name
    assert "/" not in name
    assert ":" not in name


def test_token_store_delete_removes_file(tmp_path):
    store = TokenStore(base_dir=tmp_path, prefer_keychain=False)
    store.save("srv", {"access_token": "x"})
    store.delete("srv")
    assert store.load("srv") is None


# ---------------------------------------------------------------------------
# Token exchange + refresh
# ---------------------------------------------------------------------------

def test_oauth_client_completes_authorization_and_persists(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode("ascii")
        return _fake_response({
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "token_type": "Bearer",
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    store = TokenStore(base_dir=tmp_path, prefer_keychain=False)
    client = OAuthClient(
        "srv",
        OAuthConfig(
            client_id="cid",
            authorization_endpoint="https://issuer/authorize",
            token_endpoint="https://issuer/token",
        ),
        store=store,
    )
    token = client.complete_authorization(code="auth-code", code_verifier="verifier-x")
    assert captured["url"] == "https://issuer/token"
    assert "code=auth-code" in captured["body"]
    assert "code_verifier=verifier-x" in captured["body"]
    assert "grant_type=authorization_code" in captured["body"]
    assert token["access_token"] == "new-access"
    # Was saved to disk with 0o600.
    saved = store.load("srv")
    assert saved is not None
    assert saved["access_token"] == "new-access"
    assert saved.get("expires_at", 0) > 0
    mode = stat.S_IMODE(os.stat(tmp_path / "srv.json").st_mode)
    assert mode == 0o600


def test_oauth_client_refreshes_expired_token(tmp_path, monkeypatch):
    store = TokenStore(base_dir=tmp_path, prefer_keychain=False)
    # Pre-seed an expired token.
    store.save("srv", {
        "access_token": "old",
        "refresh_token": "rt",
        "expires_at": 1,
    })
    refresh_calls: dict = {}

    def fake_urlopen(req, timeout=30):
        refresh_calls["body"] = req.data.decode("ascii")
        return _fake_response({
            "access_token": "fresh",
            "expires_in": 3600,
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = OAuthClient(
        "srv",
        OAuthConfig(
            client_id="cid",
            token_endpoint="https://issuer/token",
            authorization_endpoint="https://issuer/authorize",
        ),
        store=store,
    )
    tok = client.access_token()
    assert tok == "fresh"
    assert "grant_type=refresh_token" in refresh_calls["body"]
    assert "refresh_token=rt" in refresh_calls["body"]
    # The original refresh_token is preserved when the server omits it.
    saved = store.load("srv")
    assert saved is not None
    assert saved["refresh_token"] == "rt"


def test_oauth_client_handle_unauthorized_returns_none_without_refresh(tmp_path):
    store = TokenStore(base_dir=tmp_path, prefer_keychain=False)
    store.save("srv", {"access_token": "old"})  # no refresh_token
    client = OAuthClient(
        "srv",
        OAuthConfig(client_id="cid", token_endpoint="https://issuer/token"),
        store=store,
    )
    assert client.handle_unauthorized() is None


def test_access_token_returns_none_when_no_token(tmp_path):
    store = TokenStore(base_dir=tmp_path, prefer_keychain=False)
    client = OAuthClient(
        "srv",
        OAuthConfig(client_id="cid", token_endpoint="https://issuer/token"),
        store=store,
    )
    assert client.access_token() is None


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------

def test_oauth_config_from_dict_uses_callback_port():
    cfg = oauth_config_from_dict({
        "clientId": "abc",
        "callbackPort": 9999,
        "scopes": ["read"],
    })
    assert cfg.client_id == "abc"
    assert cfg.redirect_uri == "http://127.0.0.1:9999/callback"
    assert cfg.scopes == ["read"]


def test_oauth_config_from_dict_metadata_url():
    cfg = oauth_config_from_dict({
        "clientId": "abc",
        "authServerMetadataUrl":
            "https://issuer/.well-known/oauth-authorization-server",
    })
    assert cfg.auth_server_metadata_url.startswith("https://issuer/")


def test_token_store_force_file_when_keychain_disabled(tmp_path, monkeypatch):
    # Even on macOS with `security` available, prefer_keychain=False uses files.
    monkeypatch.setattr(oauth_mod.shutil, "which", lambda *_: "/usr/bin/security")
    store = TokenStore(base_dir=tmp_path, prefer_keychain=False)
    store.save("srv", {"access_token": "x"})
    assert (tmp_path / "srv.json").exists()

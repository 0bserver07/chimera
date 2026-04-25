"""OAuth2 PKCE flow for remote MCP servers.

Implements the subset of RFC 7636 (PKCE) and RFC 8414 (OAuth Authorization
Server Metadata) needed to authenticate against a remote MCP server. Tokens
are persisted via the macOS Keychain when ``security`` is on PATH, otherwise
to ``~/.chimera/tokens/<server>.json`` with mode ``0o600``.

This module is intentionally stdlib-only — it is meant to be importable as
part of the core distribution even when the optional ``mcp`` extra (which
brings ``websockets`` for the WS transport) is not installed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


KEYCHAIN_SERVICE = "chimera-mcp-oauth"


# ---------------------------------------------------------------------------
# PKCE primitives
# ---------------------------------------------------------------------------

def generate_pkce_pair() -> tuple[str, str]:
    """Generate an RFC 7636 PKCE ``(code_verifier, code_challenge)`` pair.

    Verifier is a 43-128 char URL-safe random string; challenge is the
    URL-safe base64-encoded SHA-256 of the verifier with padding stripped.

    Returns:
        Tuple of ``(code_verifier, code_challenge)``.
    """
    raw = secrets.token_urlsafe(64)[:96]
    verifier = re.sub(r"[^A-Za-z0-9._~-]", "", raw)[:128]
    if len(verifier) < 43:
        verifier = (verifier + secrets.token_urlsafe(43))[:43]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_metadata(issuer_or_url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch OAuth Authorization Server Metadata (RFC 8414).

    Accepts either an issuer URL (``https://issuer.example``) — the function
    appends ``/.well-known/oauth-authorization-server`` — or the full
    metadata URL.

    Args:
        issuer_or_url: Issuer or fully-qualified discovery URL.
        timeout: Network timeout in seconds.

    Returns:
        Parsed metadata dict (must include ``authorization_endpoint`` and
        ``token_endpoint``).
    """
    if "/.well-known/" in issuer_or_url:
        url = issuer_or_url
    else:
        url = issuer_or_url.rstrip("/") + "/.well-known/oauth-authorization-server"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    return data


# ---------------------------------------------------------------------------
# Token store (keychain or file)
# ---------------------------------------------------------------------------

@dataclass
class TokenStore:
    """Persists tokens per server, preferring macOS Keychain.

    Args:
        base_dir: Directory used when the keychain is unavailable. Defaults
            to ``~/.chimera/tokens``.
        prefer_keychain: If False, always use file storage (useful for tests).
    """

    base_dir: Path = field(default_factory=lambda: Path.home() / ".chimera" / "tokens")
    prefer_keychain: bool = True

    def __post_init__(self) -> None:
        self.base_dir = Path(self.base_dir)

    def _use_keychain(self) -> bool:
        return self.prefer_keychain and shutil.which("security") is not None

    def _file_path(self, server: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", server)
        return self.base_dir / f"{safe}.json"

    def save(self, server: str, token: dict[str, Any]) -> None:
        """Persist ``token`` for ``server``."""
        payload = json.dumps(token)
        if self._use_keychain():
            try:
                # Replace any prior entry to avoid duplicate keychain items.
                subprocess.run(
                    ["security", "delete-generic-password",
                     "-s", KEYCHAIN_SERVICE, "-a", server],
                    capture_output=True, check=False,
                )
                subprocess.run(
                    ["security", "add-generic-password",
                     "-s", KEYCHAIN_SERVICE, "-a", server,
                     "-w", payload, "-U"],
                    capture_output=True, check=True,
                )
                return
            except Exception:
                pass  # fall through to file
        path = self._file_path(server)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write atomically with restrictive permissions.
        tmp = path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, path)
        os.chmod(path, 0o600)

    def load(self, server: str) -> dict[str, Any] | None:
        """Return the stored token dict for ``server``, or ``None``."""
        if self._use_keychain():
            try:
                result = subprocess.run(
                    ["security", "find-generic-password",
                     "-s", KEYCHAIN_SERVICE, "-a", server, "-w"],
                    capture_output=True, check=True, text=True,
                )
                parsed: dict[str, Any] = json.loads(result.stdout.strip())
                return parsed
            except Exception:
                pass
        path = self._file_path(server)
        if not path.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(path.read_text())
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def delete(self, server: str) -> None:
        """Remove the stored token for ``server``, if any."""
        if self._use_keychain():
            subprocess.run(
                ["security", "delete-generic-password",
                 "-s", KEYCHAIN_SERVICE, "-a", server],
                capture_output=True, check=False,
            )
        path = self._file_path(server)
        if path.exists():
            path.unlink()


# ---------------------------------------------------------------------------
# Authorization-code (PKCE) helpers
# ---------------------------------------------------------------------------

def build_authorize_url(
    auth_endpoint: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    scopes: list[str] | None = None,
    state: str | None = None,
) -> str:
    """Build the URL the user must visit to grant authorization."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    if state:
        params["state"] = state
    sep = "&" if "?" in auth_endpoint else "?"
    return f"{auth_endpoint}{sep}{urllib.parse.urlencode(params)}"


def exchange_code_for_token(
    token_endpoint: str,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Trade an authorization ``code`` + PKCE ``verifier`` for a token bundle."""
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }).encode("ascii")
    req = urllib.request.Request(
        token_endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    if "expires_in" in data:
        data["expires_at"] = int(time.time()) + int(data["expires_in"])
    return data


def refresh_token(
    token_endpoint: str,
    client_id: str,
    refresh_token_value: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Use a refresh token to obtain a fresh access token."""
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token_value,
    }).encode("ascii")
    req = urllib.request.Request(
        token_endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
    if "expires_in" in data:
        data["expires_at"] = int(time.time()) + int(data["expires_in"])
    # Some servers omit refresh_token on refresh; keep the prior one.
    data.setdefault("refresh_token", refresh_token_value)
    return data


# ---------------------------------------------------------------------------
# High-level facade
# ---------------------------------------------------------------------------

@dataclass
class OAuthConfig:
    """Configuration extracted from ``.mcp.json`` ``oauth`` field."""

    client_id: str
    auth_server_metadata_url: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    redirect_uri: str = "http://127.0.0.1:7777/callback"
    scopes: list[str] = field(default_factory=list)


class OAuthClient:
    """Coordinate PKCE discovery, token storage, and refresh-on-401.

    Args:
        server_name: Logical identifier used as the token-store key.
        config: OAuth parameters from the ``.mcp.json`` block.
        store: Optional :class:`TokenStore`; a default is created when omitted.
    """

    def __init__(
        self,
        server_name: str,
        config: OAuthConfig,
        store: TokenStore | None = None,
    ) -> None:
        self.server_name = server_name
        self.config = config
        self.store = store or TokenStore()
        self._metadata: dict[str, Any] | None = None

    # -- discovery ---------------------------------------------------

    def metadata(self) -> dict[str, Any]:
        """Lazily fetch (and cache) the authorization-server metadata."""
        if self._metadata is not None:
            return self._metadata
        if self.config.auth_server_metadata_url:
            self._metadata = discover_metadata(self.config.auth_server_metadata_url)
        else:
            self._metadata = {
                "authorization_endpoint": self.config.authorization_endpoint,
                "token_endpoint": self.config.token_endpoint,
            }
        return self._metadata

    def _endpoint(self, key: str) -> str:
        meta = self.metadata()
        url = meta.get(key)
        if not url:
            raise ValueError(f"OAuth metadata missing '{key}' for {self.server_name}")
        return str(url)

    # -- PKCE start --------------------------------------------------

    def begin_authorization(self) -> tuple[str, str]:
        """Return ``(authorize_url, code_verifier)`` to start the user-visible flow."""
        verifier, challenge = generate_pkce_pair()
        url = build_authorize_url(
            auth_endpoint=self._endpoint("authorization_endpoint"),
            client_id=self.config.client_id,
            redirect_uri=self.config.redirect_uri,
            code_challenge=challenge,
            scopes=self.config.scopes or None,
        )
        return url, verifier

    def complete_authorization(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Finish the PKCE flow once the user-supplied ``code`` is in hand."""
        token = exchange_code_for_token(
            token_endpoint=self._endpoint("token_endpoint"),
            client_id=self.config.client_id,
            code=code,
            code_verifier=code_verifier,
            redirect_uri=self.config.redirect_uri,
        )
        self.store.save(self.server_name, token)
        return token

    # -- access-token plumbing --------------------------------------

    def access_token(self) -> str | None:
        """Return a valid bearer token, refreshing if expired."""
        token = self.store.load(self.server_name)
        if token is None:
            return None
        exp = token.get("expires_at")
        if isinstance(exp, (int, float)) and exp <= time.time() + 30:
            refreshed = self._try_refresh(token)
            if refreshed is not None:
                token = refreshed
        return token.get("access_token")

    def handle_unauthorized(self) -> str | None:
        """Force a refresh after a 401; return the new access token, or ``None``."""
        token = self.store.load(self.server_name)
        if token is None:
            return None
        refreshed = self._try_refresh(token)
        return None if refreshed is None else refreshed.get("access_token")

    def _try_refresh(self, token: dict[str, Any]) -> dict[str, Any] | None:
        rt = token.get("refresh_token")
        if not rt:
            return None
        try:
            new_token = refresh_token(
                token_endpoint=self._endpoint("token_endpoint"),
                client_id=self.config.client_id,
                refresh_token_value=rt,
            )
        except urllib.error.HTTPError:
            return None
        except Exception:
            return None
        self.store.save(self.server_name, new_token)
        return new_token


def oauth_config_from_dict(spec: dict[str, Any]) -> OAuthConfig:
    """Translate a ``.mcp.json`` ``oauth`` block into an :class:`OAuthConfig`."""
    return OAuthConfig(
        client_id=spec["clientId"],
        auth_server_metadata_url=spec.get("authServerMetadataUrl"),
        authorization_endpoint=spec.get("authorizationEndpoint"),
        token_endpoint=spec.get("tokenEndpoint"),
        redirect_uri=spec.get(
            "redirectUri",
            f"http://127.0.0.1:{spec.get('callbackPort', 7777)}/callback",
        ),
        scopes=list(spec.get("scopes", [])),
    )

"""OAuth 2.0 authentication flows using only stdlib."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from chimera.auth.base import AuthProvider, Credential

__all__ = ["OAuthBrowserFlow", "OAuthDeviceFlow"]


class OAuthDeviceFlow(AuthProvider):
    """OAuth 2.0 device authorization grant flow (RFC 8628).

    Shows a code in the terminal; the user visits a URL to authorize.
    Uses only stdlib — no external dependencies required.
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
        """Run the device authorization flow and return a credential.

        Returns:
            A Credential with access_token (and refresh_token if provided).

        Raises:
            TimeoutError: If the user does not authorize within the timeout.
            urllib.error.HTTPError: On unexpected HTTP errors from the provider.
        """
        # Step 1: Request device code
        data = urllib.parse.urlencode({
            "client_id": self._client_id,
            "scope": "openid",
        }).encode()
        req = urllib.request.Request(
            self._device_auth_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = urllib.request.urlopen(req)
        device_data = json.loads(resp.read())

        device_code = device_data["device_code"]
        user_code = device_data["user_code"]
        verification_uri = device_data.get("verification_uri") or device_data.get(
            "verification_url", ""
        )
        interval = device_data.get("interval", self._poll_interval)

        print(f"\nVisit: {verification_uri}")
        print(f"Enter code: {user_code}\n")

        # Step 2: Poll for token
        start = time.time()
        while time.time() - start < self._timeout:
            time.sleep(interval)
            try:
                token_data = urllib.parse.urlencode({
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self._client_id,
                    "device_code": device_code,
                }).encode()
                token_req = urllib.request.Request(
                    self._token_url,
                    data=token_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                token_resp = urllib.request.urlopen(token_req)
                token_info = json.loads(token_resp.read())

                if "access_token" in token_info:
                    expires_in = token_info.get("expires_in", 3600)
                    return Credential(
                        provider=self._provider_name,
                        token=token_info["access_token"],
                        refresh_token=token_info.get("refresh_token"),
                        expires_at=time.time() + expires_in,
                    )
            except urllib.error.HTTPError as e:
                body = e.read().decode()
                if "authorization_pending" in body or "slow_down" in body:
                    if "slow_down" in body:
                        interval += 5
                    continue
                raise

        raise TimeoutError(f"OAuth device flow timed out after {self._timeout}s")

    def refresh(self, credential: Credential) -> Credential:
        """Refresh an existing credential using the refresh token.

        Args:
            credential: The credential to refresh.

        Returns:
            A new Credential with a fresh access token.

        Raises:
            ValueError: If the credential has no refresh token.
        """
        if not credential.refresh_token:
            raise ValueError("No refresh token available")
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "refresh_token": credential.refresh_token,
        }).encode()
        req = urllib.request.Request(
            self._token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = urllib.request.urlopen(req)
        token_info = json.loads(resp.read())
        expires_in = token_info.get("expires_in", 3600)
        return Credential(
            provider=self._provider_name,
            token=token_info["access_token"],
            refresh_token=token_info.get("refresh_token", credential.refresh_token),
            expires_at=time.time() + expires_in,
        )


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth redirect callback."""

    auth_code: str | None = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        if code:
            _CallbackHandler.auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Authorization successful!</h1>"
                b"<p>You can close this tab.</p></body></html>"
            )
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h1>Error: {error}</h1></body></html>".encode()
            )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # Suppress server logs


class OAuthBrowserFlow(AuthProvider):
    """OAuth 2.0 authorization code flow with PKCE and local redirect.

    Opens the browser, starts a local callback server, and exchanges the
    authorization code for an access token. Uses only stdlib — no external
    dependencies required.
    """

    def __init__(
        self,
        provider_name: str,
        client_id: str,
        auth_url: str,
        token_url: str,
        redirect_port: int = 19876,
        scopes: str = "openid",
    ) -> None:
        self._provider_name = provider_name
        self._client_id = client_id
        self._auth_url = auth_url
        self._token_url = token_url
        self._redirect_port = redirect_port
        self._scopes = scopes

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def authenticate(self) -> Credential:
        """Run the browser authorization code + PKCE flow.

        Returns:
            A Credential with access_token (and refresh_token if provided).

        Raises:
            urllib.error.HTTPError: On unexpected HTTP errors from the provider.
        """
        import base64

        # PKCE: generate code_verifier and code_challenge
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge_b64 = (
            base64.urlsafe_b64encode(code_challenge).rstrip(b"=").decode()
        )

        redirect_uri = f"http://127.0.0.1:{self._redirect_port}/callback"
        state = secrets.token_urlsafe(32)

        # Build authorization URL
        params = urllib.parse.urlencode({
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": self._scopes,
            "state": state,
            "code_challenge": code_challenge_b64,
            "code_challenge_method": "S256",
        })
        auth_full_url = f"{self._auth_url}?{params}"

        # Start local callback server
        _CallbackHandler.auth_code = None
        server = HTTPServer(("127.0.0.1", self._redirect_port), _CallbackHandler)
        server.timeout = 300

        # Open browser
        print("\nOpening browser for authorization...")
        print(f"If browser doesn't open, visit: {auth_full_url}\n")
        webbrowser.open(auth_full_url)

        # Wait for callback
        while _CallbackHandler.auth_code is None:
            server.handle_request()

        server.server_close()
        auth_code = _CallbackHandler.auth_code

        # Exchange code for token
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "code": auth_code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }).encode()
        req = urllib.request.Request(
            self._token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = urllib.request.urlopen(req)
        token_info = json.loads(resp.read())

        expires_in = token_info.get("expires_in", 3600)
        return Credential(
            provider=self._provider_name,
            token=token_info["access_token"],
            refresh_token=token_info.get("refresh_token"),
            expires_at=time.time() + expires_in,
        )

    def refresh(self, credential: Credential) -> Credential:
        """Refresh an existing credential using the refresh token.

        Args:
            credential: The credential to refresh.

        Returns:
            A new Credential with a fresh access token.

        Raises:
            ValueError: If the credential has no refresh token.
        """
        if not credential.refresh_token:
            raise ValueError("No refresh token available")
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "refresh_token": credential.refresh_token,
        }).encode()
        req = urllib.request.Request(
            self._token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = urllib.request.urlopen(req)
        token_info = json.loads(resp.read())
        expires_in = token_info.get("expires_in", 3600)
        return Credential(
            provider=self._provider_name,
            token=token_info["access_token"],
            refresh_token=token_info.get("refresh_token", credential.refresh_token),
            expires_at=time.time() + expires_in,
        )

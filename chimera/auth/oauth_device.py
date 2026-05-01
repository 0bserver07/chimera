"""OAuth 2.0 device authorization grant (RFC 8628) — provider-aware front end.

This module sits on top of :mod:`chimera.auth.oauth` and adds:

* Per-provider preset endpoints (OpenRouter, xAI, Anthropic, OpenAI) so users
  can run ``chimera auth login <provider>`` without copying URLs.
* A user-friendly device flow that prints the verification URL plus the
  one-time code, optionally copies the code to the system clipboard, and
  persists the resulting :class:`~chimera.auth.base.Credential` via
  :class:`~chimera.auth.store.CredentialStore`.

It is implemented entirely against ``urllib.request`` so the core library
remains stdlib-only. Some provider IDs (Anthropic, OpenAI) do not currently
publish a public device-flow client; their entries are scaffolded with
documented placeholder endpoints that surface a clear error if a user tries
to run them without supplying their own ``client_id`` / endpoints.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from chimera.auth.base import AuthProvider, Credential
from chimera.auth.store import CredentialStore

__all__ = [
    "OAuthDeviceFlow",
    "DeviceFlowError",
    "DeviceFlowPreset",
    "PROVIDER_PRESETS",
    "login",
    "copy_to_clipboard",
]


class DeviceFlowError(RuntimeError):
    """Raised when the device flow rejects, slows, or otherwise fails."""


@dataclass(frozen=True)
class DeviceFlowPreset:
    """Static configuration for a known provider's device flow."""

    provider: str
    client_id: str
    device_url: str
    token_url: str
    scopes: list[str] = field(default_factory=list)
    # When True, the provider entry is a documented placeholder and the user
    # must supply their own client_id / endpoints to run the flow.
    placeholder: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------
# WHY: published OAuth device-flow endpoints differ per provider. We bake in
# the ones with a public client (OpenRouter, xAI) and scaffold the rest with
# placeholders + notes so the CLI can still surface a useful error.

PROVIDER_PRESETS: dict[str, DeviceFlowPreset] = {
    "openrouter": DeviceFlowPreset(
        provider="openrouter",
        client_id="chimera-cli",
        device_url="https://openrouter.ai/api/v1/auth/device/code",
        token_url="https://openrouter.ai/api/v1/auth/device/token",
        scopes=["completion"],
        notes="OpenRouter device flow (public client).",
    ),
    "xai": DeviceFlowPreset(
        provider="xai",
        client_id="chimera-cli",
        device_url="https://api.x.ai/oauth/device/code",
        token_url="https://api.x.ai/oauth/device/token",
        scopes=["api"],
        notes="xAI device flow (public client).",
    ),
    "anthropic": DeviceFlowPreset(
        provider="anthropic",
        client_id="",
        device_url="",
        token_url="",
        scopes=[],
        placeholder=True,
        notes=(
            "Anthropic does not publish a device-flow client at this time. "
            "Use ANTHROPIC_API_KEY or 'chimera auth login --client-id ... "
            "--device-url ... --token-url ...' to override."
        ),
    ),
    "openai": DeviceFlowPreset(
        provider="openai",
        client_id="",
        device_url="",
        token_url="",
        scopes=[],
        placeholder=True,
        notes=(
            "OpenAI does not publish a device-flow client at this time. "
            "Use OPENAI_API_KEY or 'chimera auth login --client-id ... "
            "--device-url ... --token-url ...' to override."
        ),
    ),
}


def copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard copy. Returns True if something accepted the text.

    Tries ``pbcopy`` (macOS), ``xclip`` and ``xsel`` (Linux), then ``clip``
    (Windows). All failures are swallowed; clipboard support is a nicety.
    """
    candidates: list[list[str]] = []
    if sys.platform == "darwin" and shutil.which("pbcopy"):
        candidates.append(["pbcopy"])
    if shutil.which("xclip"):
        candidates.append(["xclip", "-selection", "clipboard"])
    if shutil.which("xsel"):
        candidates.append(["xsel", "--clipboard", "--input"])
    if sys.platform == "win32" and shutil.which("clip"):
        candidates.append(["clip"])

    for cmd in candidates:
        try:
            proc = subprocess.run(
                cmd,
                input=text.encode(),
                check=False,
                timeout=2,
            )
            if proc.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------


class OAuthDeviceFlow(AuthProvider):
    """Device-authorization flow (RFC 8628) with clipboard + storage.

    Args:
        provider: Logical provider id (``"openrouter"``, ``"xai"``, ...). This
            is the key under which the resulting credential is stored.
        client_id: OAuth client id registered with the provider.
        scopes: List of scope strings; joined with spaces on the wire.
        device_url: POST endpoint that returns ``device_code`` /
            ``user_code`` / ``verification_uri``.
        token_url: POST endpoint to poll until the user authorizes.
        poll_interval: Initial polling interval in seconds (overridden by
            the provider's ``interval`` if returned, raised by
            ``slow_down``).
        timeout: Maximum total wait in seconds.
        store: Optional :class:`CredentialStore` for persistence. If ``None``
            a default ``~/.chimera/credentials.json`` store is used.
        clipboard: When True (default) the user_code is copied to the
            clipboard if a copy tool is available.
        opener: Override for ``urllib.request.urlopen`` (used by tests).
        sleep: Override for ``time.sleep`` (used by tests).
    """

    def __init__(
        self,
        provider: str,
        client_id: str,
        scopes: list[str],
        device_url: str,
        token_url: str,
        poll_interval: int = 5,
        timeout: int = 300,
        store: CredentialStore | None = None,
        clipboard: bool = True,
        opener: Optional[Callable[[urllib.request.Request], Any]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        if not provider:
            raise ValueError("provider must be a non-empty string")
        if not client_id:
            raise ValueError(
                f"client_id is required for provider '{provider}' — "
                "supply --client-id or pick a provider with a published client."
            )
        if not device_url or not token_url:
            raise ValueError(
                f"device_url and token_url are required for provider '{provider}'."
            )
        self._provider = provider
        self._client_id = client_id
        self._scopes = list(scopes)
        self._device_url = device_url
        self._token_url = token_url
        self._poll_interval = max(0, int(poll_interval))
        self._timeout = max(0, int(timeout))
        self._store = store if store is not None else CredentialStore()
        self._clipboard = clipboard
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleep or time.sleep

    # ------------------------------------------------------------------
    # AuthProvider API
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return self._provider

    @classmethod
    def from_preset(
        cls,
        provider: str,
        *,
        client_id: str | None = None,
        scopes: list[str] | None = None,
        device_url: str | None = None,
        token_url: str | None = None,
        **kwargs: Any,
    ) -> OAuthDeviceFlow:
        """Build a flow from :data:`PROVIDER_PRESETS`, allowing per-call overrides."""
        preset = PROVIDER_PRESETS.get(provider)
        if preset is None:
            raise KeyError(
                f"Unknown provider '{provider}'. "
                f"Known: {sorted(PROVIDER_PRESETS)}"
            )
        cid = client_id or preset.client_id
        d_url = device_url or preset.device_url
        t_url = token_url or preset.token_url
        scope_list = list(scopes) if scopes is not None else list(preset.scopes)
        if preset.placeholder and not (client_id and device_url and token_url):
            raise DeviceFlowError(
                f"Provider '{provider}' is a scaffolded placeholder. "
                f"{preset.notes}"
            )
        return cls(
            provider=preset.provider,
            client_id=cid,
            scopes=scope_list,
            device_url=d_url,
            token_url=t_url,
            **kwargs,
        )

    def authenticate(self) -> Credential:
        """Run the device flow end-to-end, persist the credential, return it."""
        device_data = self._request_device_code()
        device_code = device_data["device_code"]
        user_code = device_data["user_code"]
        verification_uri = device_data.get("verification_uri") or device_data.get(
            "verification_url", ""
        )
        interval = int(device_data.get("interval", self._poll_interval))

        copied = False
        if self._clipboard:
            copied = copy_to_clipboard(user_code)

        suffix = " (copied to clipboard)" if copied else ""
        print(f"\nVisit {verification_uri}, enter code: {user_code}{suffix}\n")

        cred = self._poll_for_token(device_code, interval)
        self._store.save(cred)
        return cred

    def refresh(self, credential: Credential) -> Credential:
        """Refresh an existing credential. Persists the refreshed value."""
        if not credential.refresh_token:
            raise ValueError("No refresh token available")
        body = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": credential.refresh_token,
            }
        ).encode()
        req = urllib.request.Request(
            self._token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp = self._opener(req)
        info = json.loads(resp.read())
        expires_in = int(info.get("expires_in", 3600))
        new_cred = Credential(
            provider=self._provider,
            token=info["access_token"],
            refresh_token=info.get("refresh_token", credential.refresh_token),
            expires_at=time.time() + expires_in,
        )
        self._store.save(new_cred)
        return new_cred

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _request_device_code(self) -> dict[str, Any]:
        params: dict[str, str] = {"client_id": self._client_id}
        if self._scopes:
            params["scope"] = " ".join(self._scopes)
        body = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            self._device_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            resp = self._opener(req)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            raise DeviceFlowError(
                f"device_code request failed ({exc.code}): {raw}"
            ) from exc
        data: dict[str, Any] = json.loads(resp.read())
        if "device_code" not in data or "user_code" not in data:
            raise DeviceFlowError(
                f"malformed device_code response: missing fields in {sorted(data)}"
            )
        return data

    def _poll_for_token(self, device_code: str, interval: int) -> Credential:
        start = time.time()
        deadline = start + self._timeout
        cur_interval = max(0, interval)
        while True:
            now = time.time()
            if now >= deadline:
                raise TimeoutError(
                    f"OAuth device flow timed out after {self._timeout}s"
                )
            self._sleep(cur_interval)
            try:
                body = urllib.parse.urlencode(
                    {
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "client_id": self._client_id,
                        "device_code": device_code,
                    }
                ).encode()
                req = urllib.request.Request(
                    self._token_url,
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp = self._opener(req)
                info = json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode(errors="replace")
                if "authorization_pending" in raw:
                    continue
                if "slow_down" in raw:
                    cur_interval += 5
                    continue
                if "access_denied" in raw:
                    raise DeviceFlowError("user denied authorization") from exc
                if "expired_token" in raw:
                    raise DeviceFlowError("device code expired") from exc
                raise DeviceFlowError(
                    f"token poll failed ({exc.code}): {raw}"
                ) from exc

            if "access_token" not in info:
                # Provider returned 200 but no token (e.g. {"error":"authorization_pending"})
                err = info.get("error", "")
                if err in ("authorization_pending", ""):
                    continue
                if err == "slow_down":
                    cur_interval += 5
                    continue
                if err == "access_denied":
                    raise DeviceFlowError("user denied authorization")
                if err == "expired_token":
                    raise DeviceFlowError("device code expired")
                raise DeviceFlowError(f"token poll error: {info}")

            expires_in = int(info.get("expires_in", 3600))
            return Credential(
                provider=self._provider,
                token=info["access_token"],
                refresh_token=info.get("refresh_token"),
                expires_at=time.time() + expires_in,
            )


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------


def login(
    provider: str,
    *,
    client_id: str | None = None,
    device_url: str | None = None,
    token_url: str | None = None,
    scopes: list[str] | None = None,
    store: CredentialStore | None = None,
    clipboard: bool = True,
) -> Credential:
    """High-level helper used by the ``chimera auth login`` subcommand.

    Looks up the preset for *provider*, applies any explicit overrides, runs
    the flow, and returns the resulting credential. The credential is also
    persisted via :class:`CredentialStore`.
    """
    flow = OAuthDeviceFlow.from_preset(
        provider,
        client_id=client_id,
        scopes=scopes,
        device_url=device_url,
        token_url=token_url,
        store=store,
        clipboard=clipboard,
    )
    return flow.authenticate()

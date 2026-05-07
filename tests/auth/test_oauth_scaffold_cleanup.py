"""Tests for the W11-B3 OAuth scaffold cleanup.

anthropic and openai do not publish public OAuth device-flow clients. Running
``chimera auth login anthropic`` (or ``openai``) without explicit
client_id/device_url/token_url overrides must:

- Exit with rc=2.
- Print a friendly stderr message that mentions the API-key env var and the
  provider's API-key console URL.
- Not open a browser, not poll for tokens, not hit any HTTP endpoint.

openrouter and xai must keep working unchanged.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.auth.oauth_device import (
    PROVIDER_PRESETS,
    SCAFFOLD_PROVIDERS,
    scaffold_message,
)
from chimera.auth.store import CredentialStore
from chimera.cli.main import run_auth


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Redirect the CredentialStore default path into tmp_path so tests never
    touch the user's ~/.chimera/credentials.json. Yields the resolved path."""
    store_path = tmp_path / "creds.json"
    real_init = CredentialStore.__init__

    def fake_init(self, path: str = str(store_path)) -> None:
        real_init(self, path)

    monkeypatch.setattr(CredentialStore, "__init__", fake_init)
    return store_path


def _login_args(
    provider: str,
    *,
    client_id: str | None = None,
    device_url: str | None = None,
    token_url: str | None = None,
    scope: list[str] | None = None,
    no_clipboard: bool = True,
) -> argparse.Namespace:
    """Build a Namespace identical to what argparse hands to run_auth()."""
    return argparse.Namespace(
        command="auth",
        auth_command="login",
        provider=provider,
        client_id=client_id,
        device_url=device_url,
        token_url=token_url,
        scope=scope,
        no_clipboard=no_clipboard,
    )


def _json_resp(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    return resp


def _scripted_opener(events: list[Any]):
    """urlopen mock yielding one event per call (200 JSON or HTTPError)."""
    idx = [0]

    def opener(req):  # noqa: ANN001
        i = idx[0]
        idx[0] += 1
        if i >= len(events):
            i = len(events) - 1
        ev = events[i]
        if isinstance(ev, urllib.error.HTTPError):
            raise ev
        return _json_resp(ev)

    return opener


# ---------------------------------------------------------------------------
# scaffold providers — friendly bail
# ---------------------------------------------------------------------------


def test_anthropic_login_scaffold(isolated_store, capsys, monkeypatch):
    """`chimera auth login anthropic` exits rc=2 with a helpful stderr message
    and does not attempt any HTTP request."""

    def explode(*_a, **_kw):  # noqa: ANN002, ANN003
        raise AssertionError("scaffold path must not make HTTP calls")

    monkeypatch.setattr(
        "chimera.auth.oauth_device.urllib.request.urlopen", explode
    )

    rc = run_auth(_login_args("anthropic"))
    assert rc == 2
    captured = capsys.readouterr()
    assert "anthropic does not have a public OAuth device flow" in captured.err
    assert "ANTHROPIC_API_KEY" in captured.err
    assert "console.anthropic.com" in captured.err
    # Nothing should have been written to the credential store.
    assert not isolated_store.exists()


def test_openai_login_scaffold(isolated_store, capsys, monkeypatch):
    """Same as test_anthropic_login_scaffold but for openai."""

    def explode(*_a, **_kw):  # noqa: ANN002, ANN003
        raise AssertionError("scaffold path must not make HTTP calls")

    monkeypatch.setattr(
        "chimera.auth.oauth_device.urllib.request.urlopen", explode
    )

    rc = run_auth(_login_args("openai"))
    assert rc == 2
    captured = capsys.readouterr()
    assert "openai does not have a public OAuth device flow" in captured.err
    assert "OPENAI_API_KEY" in captured.err
    assert "platform.openai.com/api-keys" in captured.err
    assert not isolated_store.exists()


def test_scaffold_message_contains_api_key_hint():
    """Programmatic check on scaffold_message() — the friendly text must
    name the env var and include a console URL for both anthropic and openai."""
    a = scaffold_message("anthropic")
    assert "ANTHROPIC_API_KEY" in a
    assert "https://console.anthropic.com" in a
    assert "export ANTHROPIC_API_KEY=" in a

    o = scaffold_message("openai")
    assert "OPENAI_API_KEY" in o
    assert "https://platform.openai.com/api-keys" in o
    assert "export OPENAI_API_KEY=" in o


def test_scaffold_providers_mapping_complete():
    """Both anthropic and openai must be present in SCAFFOLD_PROVIDERS and
    each entry must carry the env-var + console URL keys consumers rely on."""
    assert {"anthropic", "openai"} <= set(SCAFFOLD_PROVIDERS)
    for name, info in SCAFFOLD_PROVIDERS.items():
        assert info["env_var"], f"missing env_var for {name}"
        assert info["console_url"].startswith("https://"), f"bad URL for {name}"
        assert info["key_prefix"], f"missing key_prefix for {name}"


def test_scaffold_presets_have_empty_endpoints():
    """The PROVIDER_PRESETS entries for anthropic/openai must signal
    'no public flow' by leaving device_url/token_url empty (sentinel) and
    setting placeholder=True."""
    for name in ("anthropic", "openai"):
        preset = PROVIDER_PRESETS[name]
        assert preset.placeholder is True
        assert preset.device_url == ""
        assert preset.token_url == ""
        assert preset.client_id == ""


# ---------------------------------------------------------------------------
# real-flow providers — must keep working
# ---------------------------------------------------------------------------


def test_openrouter_login_still_works(isolated_store, capsys, monkeypatch):
    """openrouter is a real provider with a public device flow. Verify that
    run_auth(login) drives the full RFC 8628 flow when HTTP is mocked."""
    events = [
        # device code request
        {
            "device_code": "dev",
            "user_code": "USER1",
            "verification_uri": "https://verify",
            "interval": 0,
        },
        # poll → granted
        {"access_token": "real-token", "expires_in": 60},
    ]
    monkeypatch.setattr(
        "chimera.auth.oauth_device.urllib.request.urlopen",
        _scripted_opener(events),
    )
    monkeypatch.setattr("chimera.auth.oauth_device.time.sleep", lambda _s: None)

    rc = run_auth(_login_args("openrouter"))
    assert rc == 0
    # Credential was persisted.
    assert isolated_store.exists()
    saved = json.loads(isolated_store.read_text())
    assert saved["openrouter"]["token"] == "real-token"
    captured = capsys.readouterr()
    assert "Authenticated as 'openrouter'" in captured.out


def test_xai_login_still_works(isolated_store, capsys, monkeypatch):
    """Same as test_openrouter_login_still_works but for xai."""
    events = [
        {
            "device_code": "dev",
            "user_code": "USER1",
            "verification_uri": "https://verify",
            "interval": 0,
        },
        {"access_token": "xai-token", "expires_in": 60},
    ]
    monkeypatch.setattr(
        "chimera.auth.oauth_device.urllib.request.urlopen",
        _scripted_opener(events),
    )
    monkeypatch.setattr("chimera.auth.oauth_device.time.sleep", lambda _s: None)

    rc = run_auth(_login_args("xai"))
    assert rc == 0
    assert isolated_store.exists()
    saved = json.loads(isolated_store.read_text())
    assert saved["xai"]["token"] == "xai-token"
    captured = capsys.readouterr()
    assert "Authenticated as 'xai'" in captured.out


def test_scaffold_with_overrides_still_drives_flow(
    isolated_store, capsys, monkeypatch
):
    """If a user supplies --client-id / --device-url / --token-url for
    anthropic (or openai), they have a private client and the CLI should run
    the real device flow against the supplied endpoints, not the scaffold
    short-circuit."""
    events = [
        {
            "device_code": "dev",
            "user_code": "USER1",
            "verification_uri": "https://verify",
            "interval": 0,
        },
        {"access_token": "private-tok", "expires_in": 60},
    ]
    monkeypatch.setattr(
        "chimera.auth.oauth_device.urllib.request.urlopen",
        _scripted_opener(events),
    )
    monkeypatch.setattr("chimera.auth.oauth_device.time.sleep", lambda _s: None)

    rc = run_auth(
        _login_args(
            "anthropic",
            client_id="my-private-cid",
            device_url="https://my-private-host/oauth/device/code",
            token_url="https://my-private-host/oauth/device/token",
        )
    )
    assert rc == 0
    assert isolated_store.exists()
    saved = json.loads(isolated_store.read_text())
    assert saved["anthropic"]["token"] == "private-tok"

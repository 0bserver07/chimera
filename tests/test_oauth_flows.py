"""Tests for OAuth flows with mocked HTTP."""
from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from chimera.auth.base import Credential
from chimera.auth.oauth import OAuthBrowserFlow, OAuthDeviceFlow


def _mock_urlopen(responses):
    """Create a mock urlopen that returns responses in sequence."""
    call_count = [0]

    def _urlopen(req):
        resp = MagicMock()
        idx = min(call_count[0], len(responses) - 1)
        resp.read.return_value = json.dumps(responses[idx]).encode()
        call_count[0] += 1
        return resp

    return _urlopen


# ---------------------------------------------------------------------------
# OAuthDeviceFlow
# ---------------------------------------------------------------------------


def test_device_flow_success():
    device_resp = {
        "device_code": "dev123",
        "user_code": "ABCD-1234",
        "verification_uri": "https://example.com/verify",
        "interval": 0,
    }
    token_resp = {
        "access_token": "tok_abc",
        "refresh_token": "ref_xyz",
        "expires_in": 3600,
    }
    mock = _mock_urlopen([device_resp, token_resp])

    flow = OAuthDeviceFlow(
        provider_name="test",
        client_id="cid",
        device_auth_url="https://example.com/device",
        token_url="https://example.com/token",
        poll_interval=0,
    )

    with patch("chimera.auth.oauth.urllib.request.urlopen", mock):
        with patch("chimera.auth.oauth.time.sleep"):
            cred = flow.authenticate()

    assert cred.token == "tok_abc"
    assert cred.refresh_token == "ref_xyz"
    assert cred.provider == "test"
    assert cred.expires_at is not None
    assert cred.expires_at > time.time()


def test_device_flow_uses_verification_url_fallback():
    """verification_url (legacy) fallback when verification_uri is absent."""
    device_resp = {
        "device_code": "dev456",
        "user_code": "EFGH-5678",
        "verification_url": "https://example.com/verify-legacy",
        "interval": 0,
    }
    token_resp = {"access_token": "tok_legacy", "expires_in": 3600}
    mock = _mock_urlopen([device_resp, token_resp])

    flow = OAuthDeviceFlow(
        provider_name="test",
        client_id="cid",
        device_auth_url="https://example.com/device",
        token_url="https://example.com/token",
        poll_interval=0,
    )

    with patch("chimera.auth.oauth.urllib.request.urlopen", mock):
        with patch("chimera.auth.oauth.time.sleep"):
            cred = flow.authenticate()

    assert cred.token == "tok_legacy"


def test_device_flow_refresh():
    token_resp = {
        "access_token": "new_tok",
        "refresh_token": "new_ref",
        "expires_in": 3600,
    }
    mock = _mock_urlopen([token_resp])

    flow = OAuthDeviceFlow(
        provider_name="test",
        client_id="cid",
        device_auth_url="https://example.com/device",
        token_url="https://example.com/token",
    )
    old_cred = Credential(provider="test", token="old", refresh_token="old_ref")

    with patch("chimera.auth.oauth.urllib.request.urlopen", mock):
        cred = flow.refresh(old_cred)

    assert cred.token == "new_tok"
    assert cred.refresh_token == "new_ref"
    assert cred.provider == "test"


def test_device_flow_refresh_keeps_old_refresh_token_when_none_returned():
    """If the token endpoint doesn't return a new refresh_token, keep the old one."""
    token_resp = {
        "access_token": "fresh_tok",
        "expires_in": 3600,
        # no refresh_token in response
    }
    mock = _mock_urlopen([token_resp])

    flow = OAuthDeviceFlow(
        provider_name="test",
        client_id="cid",
        device_auth_url="https://example.com/device",
        token_url="https://example.com/token",
    )
    old_cred = Credential(provider="test", token="old", refresh_token="keep_me")

    with patch("chimera.auth.oauth.urllib.request.urlopen", mock):
        cred = flow.refresh(old_cred)

    assert cred.token == "fresh_tok"
    assert cred.refresh_token == "keep_me"


def test_device_flow_no_refresh_token_raises():
    flow = OAuthDeviceFlow(
        provider_name="test",
        client_id="cid",
        device_auth_url="https://example.com/device",
        token_url="https://example.com/token",
    )
    cred = Credential(provider="test", token="tok", refresh_token=None)
    with pytest.raises(ValueError, match="No refresh token"):
        flow.refresh(cred)


def test_device_flow_slow_down_increases_interval():
    """slow_down error should increase the polling interval by 5."""
    import urllib.error

    device_resp = {
        "device_code": "dev789",
        "user_code": "SLOW-DOWN",
        "verification_uri": "https://example.com/verify",
        "interval": 0,
    }
    token_resp = {"access_token": "tok_slow", "expires_in": 3600}

    call_count = [0]

    def mock_urlopen(req):
        resp = MagicMock()
        if call_count[0] == 0:
            # device code request
            resp.read.return_value = json.dumps(device_resp).encode()
            call_count[0] += 1
            return resp
        elif call_count[0] == 1:
            # first poll: slow_down
            call_count[0] += 1
            err = urllib.error.HTTPError(
                url="", code=400, msg="Bad Request", hdrs=None, fp=None
            )
            err.read = lambda: b'{"error": "slow_down"}'
            raise err
        else:
            # second poll: success
            resp.read.return_value = json.dumps(token_resp).encode()
            call_count[0] += 1
            return resp

    flow = OAuthDeviceFlow(
        provider_name="test",
        client_id="cid",
        device_auth_url="https://example.com/device",
        token_url="https://example.com/token",
        poll_interval=0,
        timeout=60,
    )

    with patch("chimera.auth.oauth.urllib.request.urlopen", mock_urlopen):
        with patch("chimera.auth.oauth.time.sleep"):
            cred = flow.authenticate()

    assert cred.token == "tok_slow"


def test_device_flow_timeout():
    """Should raise TimeoutError if the token is never granted."""
    device_resp = {
        "device_code": "dev_timeout",
        "user_code": "XXXX-9999",
        "verification_uri": "https://example.com/verify",
        "interval": 0,
    }

    import urllib.error

    call_count = [0]

    def mock_urlopen(req):
        resp = MagicMock()
        if call_count[0] == 0:
            resp.read.return_value = json.dumps(device_resp).encode()
            call_count[0] += 1
            return resp
        else:
            call_count[0] += 1
            err = urllib.error.HTTPError(
                url="", code=400, msg="Bad Request", hdrs=None, fp=None
            )
            err.read = lambda: b'{"error": "authorization_pending"}'
            raise err

    flow = OAuthDeviceFlow(
        provider_name="test",
        client_id="cid",
        device_auth_url="https://example.com/device",
        token_url="https://example.com/token",
        poll_interval=0,
        timeout=0,  # immediate timeout
    )

    with patch("chimera.auth.oauth.urllib.request.urlopen", mock_urlopen):
        with patch("chimera.auth.oauth.time.sleep"):
            with pytest.raises(TimeoutError, match="timed out"):
                flow.authenticate()


def test_device_flow_provider_name():
    flow = OAuthDeviceFlow(
        provider_name="mycloud",
        client_id="cid",
        device_auth_url="https://example.com/device",
        token_url="https://example.com/token",
    )
    assert flow.provider_name == "mycloud"


# ---------------------------------------------------------------------------
# OAuthBrowserFlow
# ---------------------------------------------------------------------------


def test_browser_flow_refresh():
    """Test the token refresh part of browser flow (no browser/server needed)."""
    token_resp = {
        "access_token": "browser_tok",
        "refresh_token": "browser_ref",
        "expires_in": 7200,
    }

    flow = OAuthBrowserFlow(
        provider_name="test",
        client_id="cid",
        auth_url="https://example.com/auth",
        token_url="https://example.com/token",
    )

    mock = _mock_urlopen([token_resp])
    old_cred = Credential(provider="test", token="old", refresh_token="old_ref")
    with patch("chimera.auth.oauth.urllib.request.urlopen", mock):
        cred = flow.refresh(old_cred)

    assert cred.token == "browser_tok"
    assert cred.refresh_token == "browser_ref"
    assert cred.expires_at is not None
    assert cred.expires_at > time.time()


def test_browser_flow_refresh_keeps_old_refresh_token_when_none_returned():
    token_resp = {"access_token": "new_tok", "expires_in": 3600}

    flow = OAuthBrowserFlow(
        provider_name="test",
        client_id="cid",
        auth_url="https://example.com/auth",
        token_url="https://example.com/token",
    )

    mock = _mock_urlopen([token_resp])
    old_cred = Credential(provider="test", token="old", refresh_token="keep_me")
    with patch("chimera.auth.oauth.urllib.request.urlopen", mock):
        cred = flow.refresh(old_cred)

    assert cred.token == "new_tok"
    assert cred.refresh_token == "keep_me"


def test_browser_flow_no_refresh_token_raises():
    flow = OAuthBrowserFlow(
        provider_name="test",
        client_id="cid",
        auth_url="https://example.com/auth",
        token_url="https://example.com/token",
    )
    cred = Credential(provider="test", token="tok", refresh_token=None)
    with pytest.raises(ValueError, match="No refresh token"):
        flow.refresh(cred)


def test_browser_flow_provider_name():
    flow = OAuthBrowserFlow(
        provider_name="mycloud",
        client_id="cid",
        auth_url="https://example.com/auth",
        token_url="https://example.com/token",
    )
    assert flow.provider_name == "mycloud"


def test_browser_flow_authenticate_full():
    """Test the full browser flow by mocking server, webbrowser, and urlopen."""
    import chimera.auth.oauth as oauth_module

    token_resp = {
        "access_token": "full_browser_tok",
        "refresh_token": "full_browser_ref",
        "expires_in": 3600,
    }
    mock_token = _mock_urlopen([token_resp])

    flow = OAuthBrowserFlow(
        provider_name="test",
        client_id="cid",
        auth_url="https://example.com/auth",
        token_url="https://example.com/token",
        redirect_port=19876,
    )

    # Mock HTTPServer so no real socket is opened.
    # handle_request sets the class-level auth_code so the while loop exits.
    mock_server = MagicMock()
    mock_server.timeout = 300

    def fake_handle_request():
        # Set auth_code on the real _CallbackHandler so the loop exits.
        oauth_module._CallbackHandler.auth_code = "test_auth_code_xyz"

    mock_server.handle_request.side_effect = fake_handle_request

    # Reset class state before the test
    oauth_module._CallbackHandler.auth_code = None

    with patch("chimera.auth.oauth.HTTPServer", return_value=mock_server):
        with patch("chimera.auth.oauth.webbrowser.open"):
            with patch("chimera.auth.oauth.urllib.request.urlopen", mock_token):
                cred = flow.authenticate()

    assert cred.token == "full_browser_tok"
    assert cred.refresh_token == "full_browser_ref"
    assert cred.provider == "test"


def test_browser_flow_scopes_default():
    """Default scopes should be 'openid'."""
    flow = OAuthBrowserFlow(
        provider_name="test",
        client_id="cid",
        auth_url="https://example.com/auth",
        token_url="https://example.com/token",
    )
    assert flow._scopes == "openid"


def test_browser_flow_custom_scopes():
    flow = OAuthBrowserFlow(
        provider_name="test",
        client_id="cid",
        auth_url="https://example.com/auth",
        token_url="https://example.com/token",
        scopes="openid profile email",
    )
    assert flow._scopes == "openid profile email"

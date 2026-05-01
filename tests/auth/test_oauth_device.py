"""Tests for chimera.auth.oauth_device — fully mocked (no real network)."""
from __future__ import annotations

import json
import urllib.error
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.auth.base import Credential
from chimera.auth.oauth_device import (
    PROVIDER_PRESETS,
    DeviceFlowError,
    OAuthDeviceFlow,
    copy_to_clipboard,
    login,
)
from chimera.auth.store import CredentialStore


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _http_error(status: int, body: str) -> urllib.error.HTTPError:
    err = urllib.error.HTTPError(
        url="", code=status, msg="error", hdrs=None, fp=None  # type: ignore[arg-type]
    )
    err.read = lambda: body.encode()  # type: ignore[method-assign]
    return err


def _json_resp(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    return resp


def _scripted_opener(events: list[Any]):
    """Build a urlopen mock that yields each event in order.

    Each event is either a dict (200 JSON), an HTTPError, or any callable.
    """
    idx = [0]

    def opener(req):  # noqa: ANN001
        i = idx[0]
        idx[0] += 1
        if i >= len(events):
            i = len(events) - 1
        ev = events[i]
        if isinstance(ev, urllib.error.HTTPError):
            raise ev
        if callable(ev):
            return ev(req)
        return _json_resp(ev)

    return opener


def _make_store(tmp_path) -> CredentialStore:  # noqa: ANN001
    return CredentialStore(path=str(tmp_path / "creds.json"))


# ---------------------------------------------------------------------------
# constructor validation
# ---------------------------------------------------------------------------


def test_requires_provider():
    with pytest.raises(ValueError, match="provider"):
        OAuthDeviceFlow(
            provider="",
            client_id="cid",
            scopes=[],
            device_url="https://x/d",
            token_url="https://x/t",
        )


def test_requires_client_id():
    with pytest.raises(ValueError, match="client_id"):
        OAuthDeviceFlow(
            provider="p",
            client_id="",
            scopes=[],
            device_url="https://x/d",
            token_url="https://x/t",
        )


def test_requires_urls():
    with pytest.raises(ValueError, match="device_url and token_url"):
        OAuthDeviceFlow(
            provider="p",
            client_id="cid",
            scopes=[],
            device_url="",
            token_url="",
        )


def test_provider_name_property():
    flow = OAuthDeviceFlow(
        provider="openrouter",
        client_id="cid",
        scopes=["completion"],
        device_url="https://x/d",
        token_url="https://x/t",
    )
    assert flow.provider_name == "openrouter"


# ---------------------------------------------------------------------------
# device-code request
# ---------------------------------------------------------------------------


def test_device_code_request_includes_scope_and_client(tmp_path):
    captured: dict[str, Any] = {}

    def opener(req):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode()
        return _json_resp(
            {
                "device_code": "dev",
                "user_code": "USER-CODE",
                "verification_uri": "https://verify",
                "interval": 0,
            }
        )

    flow = OAuthDeviceFlow(
        provider="openrouter",
        client_id="cid-1",
        scopes=["completion", "read"],
        device_url="https://example.com/device",
        token_url="https://example.com/token",
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener(
            [opener, {"access_token": "tok", "expires_in": 60}]
        ),
        sleep=lambda _s: None,
    )
    cred = flow.authenticate()
    assert cred.token == "tok"
    assert "client_id=cid-1" in captured["body"]
    assert "scope=completion+read" in captured["body"]
    assert captured["url"] == "https://example.com/device"


def test_device_code_response_missing_fields_raises(tmp_path):
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener([{"foo": "bar"}]),
        sleep=lambda _s: None,
    )
    with pytest.raises(DeviceFlowError, match="malformed device_code"):
        flow.authenticate()


def test_device_code_http_error_wrapped(tmp_path):
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener([_http_error(500, "boom")]),
        sleep=lambda _s: None,
    )
    with pytest.raises(DeviceFlowError, match="device_code request failed"):
        flow.authenticate()


# ---------------------------------------------------------------------------
# polling
# ---------------------------------------------------------------------------


def test_pending_then_granted(tmp_path):
    events = [
        # device code request
        {
            "device_code": "dev",
            "user_code": "ABCD",
            "verification_uri": "https://verify",
            "interval": 0,
        },
        # poll #1: pending
        _http_error(400, '{"error":"authorization_pending"}'),
        # poll #2: granted
        {
            "access_token": "TOKEN",
            "refresh_token": "REFRESH",
            "expires_in": 120,
        },
    ]
    store = _make_store(tmp_path)
    flow = OAuthDeviceFlow(
        provider="openrouter",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=store,
        clipboard=False,
        opener=_scripted_opener(events),
        sleep=lambda _s: None,
    )
    cred = flow.authenticate()
    assert cred.token == "TOKEN"
    assert cred.refresh_token == "REFRESH"
    assert cred.provider == "openrouter"
    # Was persisted
    saved = store.get("openrouter")
    assert saved is not None and saved.token == "TOKEN"


def test_slow_down_increases_interval(tmp_path):
    sleeps: list[float] = []
    events = [
        {
            "device_code": "dev",
            "user_code": "ABCD",
            "verification_uri": "https://verify",
            "interval": 1,
        },
        _http_error(400, '{"error":"slow_down"}'),
        {"access_token": "tok2", "expires_in": 60},
    ]
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener(events),
        sleep=lambda s: sleeps.append(s),
    )
    cred = flow.authenticate()
    assert cred.token == "tok2"
    assert sleeps[0] == 1
    # After slow_down, interval grew by 5 -> 6
    assert sleeps[1] == 6


def test_access_denied_raises(tmp_path):
    events = [
        {
            "device_code": "dev",
            "user_code": "ABCD",
            "verification_uri": "https://verify",
            "interval": 0,
        },
        _http_error(400, '{"error":"access_denied"}'),
    ]
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener(events),
        sleep=lambda _s: None,
    )
    with pytest.raises(DeviceFlowError, match="denied authorization"):
        flow.authenticate()


def test_expired_token_raises(tmp_path):
    events = [
        {
            "device_code": "dev",
            "user_code": "ABCD",
            "verification_uri": "https://verify",
            "interval": 0,
        },
        _http_error(400, '{"error":"expired_token"}'),
    ]
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener(events),
        sleep=lambda _s: None,
    )
    with pytest.raises(DeviceFlowError, match="expired"):
        flow.authenticate()


def test_unknown_http_error_wrapped(tmp_path):
    events = [
        {
            "device_code": "dev",
            "user_code": "ABCD",
            "verification_uri": "https://verify",
            "interval": 0,
        },
        _http_error(500, "internal"),
    ]
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener(events),
        sleep=lambda _s: None,
    )
    with pytest.raises(DeviceFlowError, match="token poll failed"):
        flow.authenticate()


def test_timeout(tmp_path):
    events = [
        {
            "device_code": "dev",
            "user_code": "ABCD",
            "verification_uri": "https://verify",
            "interval": 0,
        },
        _http_error(400, '{"error":"authorization_pending"}'),
    ]
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        timeout=0,
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener(events),
        sleep=lambda _s: None,
    )
    with pytest.raises(TimeoutError, match="timed out"):
        flow.authenticate()


def test_200_with_pending_error_keeps_polling(tmp_path):
    """Some providers return 200 + {error:authorization_pending} instead of 400."""
    events = [
        {
            "device_code": "dev",
            "user_code": "ABCD",
            "verification_uri": "https://verify",
            "interval": 0,
        },
        {"error": "authorization_pending"},
        {"access_token": "ok", "expires_in": 60},
    ]
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener(events),
        sleep=lambda _s: None,
    )
    cred = flow.authenticate()
    assert cred.token == "ok"


def test_legacy_verification_url_field(tmp_path, capsys):
    events = [
        {
            "device_code": "dev",
            "user_code": "USER1",
            "verification_url": "https://legacy.example/verify",
            "interval": 0,
        },
        {"access_token": "tok", "expires_in": 60},
    ]
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener(events),
        sleep=lambda _s: None,
    )
    flow.authenticate()
    out = capsys.readouterr().out
    assert "https://legacy.example/verify" in out
    assert "USER1" in out


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def test_refresh_returns_new_token(tmp_path):
    store = _make_store(tmp_path)
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=store,
        clipboard=False,
        opener=_scripted_opener(
            [{"access_token": "new", "refresh_token": "newR", "expires_in": 60}]
        ),
        sleep=lambda _s: None,
    )
    old = Credential(provider="p", token="old", refresh_token="oldR")
    new = flow.refresh(old)
    assert new.token == "new"
    assert new.refresh_token == "newR"
    saved = store.get("p")
    assert saved is not None and saved.token == "new"


def test_refresh_keeps_old_refresh_when_absent(tmp_path):
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=_make_store(tmp_path),
        clipboard=False,
        opener=_scripted_opener([{"access_token": "fresh", "expires_in": 60}]),
        sleep=lambda _s: None,
    )
    old = Credential(provider="p", token="old", refresh_token="keep-me")
    new = flow.refresh(old)
    assert new.refresh_token == "keep-me"


def test_refresh_without_token_raises(tmp_path):
    flow = OAuthDeviceFlow(
        provider="p",
        client_id="cid",
        scopes=[],
        device_url="https://x/d",
        token_url="https://x/t",
        store=_make_store(tmp_path),
        clipboard=False,
    )
    with pytest.raises(ValueError, match="No refresh token"):
        flow.refresh(Credential(provider="p", token="t"))


# ---------------------------------------------------------------------------
# presets / login()
# ---------------------------------------------------------------------------


def test_presets_have_known_providers():
    assert {"openrouter", "xai", "anthropic", "openai"} <= set(PROVIDER_PRESETS)


def test_from_preset_openrouter_uses_baked_in_endpoints(tmp_path):
    flow = OAuthDeviceFlow.from_preset(
        "openrouter",
        store=_make_store(tmp_path),
        clipboard=False,
    )
    assert flow.provider_name == "openrouter"
    assert flow._device_url == PROVIDER_PRESETS["openrouter"].device_url
    assert flow._token_url == PROVIDER_PRESETS["openrouter"].token_url
    assert flow._client_id == PROVIDER_PRESETS["openrouter"].client_id


def test_from_preset_unknown_raises():
    with pytest.raises(KeyError, match="Unknown provider"):
        OAuthDeviceFlow.from_preset("nope")


def test_from_preset_placeholder_requires_overrides(tmp_path):
    with pytest.raises(DeviceFlowError, match="placeholder"):
        OAuthDeviceFlow.from_preset(
            "anthropic",
            store=_make_store(tmp_path),
            clipboard=False,
        )


def test_from_preset_placeholder_with_overrides_works(tmp_path):
    flow = OAuthDeviceFlow.from_preset(
        "anthropic",
        client_id="my-cid",
        device_url="https://my/d",
        token_url="https://my/t",
        scopes=["my-scope"],
        store=_make_store(tmp_path),
        clipboard=False,
    )
    assert flow._client_id == "my-cid"
    assert flow._device_url == "https://my/d"


def test_login_helper_uses_overrides(tmp_path):
    events = [
        {
            "device_code": "dev",
            "user_code": "X",
            "verification_uri": "https://v",
            "interval": 0,
        },
        {"access_token": "tok", "expires_in": 60},
    ]
    store = _make_store(tmp_path)
    with patch(
        "chimera.auth.oauth_device.OAuthDeviceFlow.authenticate",
        autospec=True,
    ) as mock_auth:
        mock_auth.return_value = Credential(provider="openrouter", token="tok")
        cred = login(
            "openrouter",
            client_id="other-cid",
            store=store,
            clipboard=False,
        )
    assert cred.token == "tok"
    # Just sanity: we didn't actually need the events, but assert the helper
    # builds a flow that defers to authenticate().
    assert mock_auth.called
    _ = events  # silence unused


def test_login_helper_runs_full_flow(tmp_path):
    events = [
        {
            "device_code": "dev",
            "user_code": "X",
            "verification_uri": "https://v",
            "interval": 0,
        },
        {"access_token": "tok2", "expires_in": 60},
    ]
    store = _make_store(tmp_path)
    # Patch urlopen at the module level so the real authenticate() runs but
    # never makes a real request.
    with patch(
        "chimera.auth.oauth_device.urllib.request.urlopen",
        side_effect=_scripted_opener(events),
    ):
        with patch("chimera.auth.oauth_device.time.sleep"):
            cred = login("openrouter", store=store, clipboard=False)
    assert cred.token == "tok2"
    assert store.get("openrouter") is not None


# ---------------------------------------------------------------------------
# clipboard
# ---------------------------------------------------------------------------


def test_clipboard_returns_false_when_no_tools_available():
    with patch("chimera.auth.oauth_device.shutil.which", return_value=None):
        assert copy_to_clipboard("ABCD") is False


def test_clipboard_uses_subprocess(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_which(name: str):  # noqa: ANN202
        return "/usr/bin/" + name if name in ("pbcopy", "xclip", "xsel", "clip") else None

    def fake_run(cmd, input, check, timeout):  # noqa: ANN001, A002
        captured["cmd"] = cmd
        captured["input"] = input
        return MagicMock(returncode=0)

    monkeypatch.setattr("chimera.auth.oauth_device.shutil.which", fake_which)
    monkeypatch.setattr("chimera.auth.oauth_device.subprocess.run", fake_run)
    # darwin path picks pbcopy first
    monkeypatch.setattr("chimera.auth.oauth_device.sys.platform", "darwin")
    assert copy_to_clipboard("HELLO") is True
    assert captured["input"] == b"HELLO"


def test_authenticate_attempts_clipboard(tmp_path):
    events = [
        {
            "device_code": "dev",
            "user_code": "CODE-1",
            "verification_uri": "https://v",
            "interval": 0,
        },
        {"access_token": "tok", "expires_in": 60},
    ]
    with patch(
        "chimera.auth.oauth_device.copy_to_clipboard", return_value=True
    ) as cb:
        flow = OAuthDeviceFlow(
            provider="p",
            client_id="cid",
            scopes=[],
            device_url="https://x/d",
            token_url="https://x/t",
            store=_make_store(tmp_path),
            clipboard=True,
            opener=_scripted_opener(events),
            sleep=lambda _s: None,
        )
        flow.authenticate()
    cb.assert_called_once_with("CODE-1")

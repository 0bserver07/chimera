"""CompatFlags quirk-parameterization tests for the OpenAI-compat provider."""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("httpx")

import chimera.providers.compatible as compat
from chimera.providers.compatible import (
    CompatFlags,
    OpenAICompatibleProvider,
    detect_compat_flags,
)
from chimera.types import Message


class _Resp:
    def __init__(self, status_code: int = 200, payload: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


def _capture_post(responses: list[_Resp], captured: list[dict[str, Any]]):
    def post(url: str, json: dict[str, Any], headers: dict[str, str], timeout: int) -> _Resp:
        # Deep-copy: the provider's 400-retry mutates the payload in place,
        # and we must assert on what each POST actually sent at the time.
        import copy

        captured.append(copy.deepcopy(json))
        return responses.pop(0)

    return post


def test_detection_defaults_and_reasoning_models() -> None:
    assert detect_compat_flags("glm-5") == CompatFlags()
    flags = detect_compat_flags("openai/o3-mini")
    assert flags.max_tokens_field == "max_completion_tokens"
    assert flags.supports_temperature is False
    assert detect_compat_flags("gpt-5-turbo").max_tokens_field == "max_completion_tokens"


def test_payload_respects_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(compat.httpx, "post", _capture_post([_Resp()], captured))
    p = OpenAICompatibleProvider(
        model="o3-mini", base_url="https://x/v1", api_key="k"
    )  # auto-detected reasoning flags
    p.complete([Message.user("hi")], max_tokens=100)

    payload = captured[0]
    assert payload["max_completion_tokens"] == 100
    assert "max_tokens" not in payload
    assert "temperature" not in payload


def test_extra_payload_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(compat.httpx, "post", _capture_post([_Resp()], captured))
    p = OpenAICompatibleProvider(
        model="some-model",
        base_url="https://x/v1",
        api_key="k",
        flags=CompatFlags(extra_payload={"reasoning_effort": "low"}),
    )
    p.complete([Message.user("hi")], max_tokens=5)
    assert captured[0]["reasoning_effort"] == "low"
    assert captured[0]["temperature"] == 0.0  # default flags keep temperature


def test_400_retries_with_alternate_max_tokens_field(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    responses = [
        _Resp(status_code=400, text=json.dumps({"error": "use max_completion_tokens"})),
        _Resp(),
    ]
    monkeypatch.setattr(compat.httpx, "post", _capture_post(responses, captured))
    p = OpenAICompatibleProvider(model="mystery-model", base_url="https://x/v1", api_key="k")

    res = p.complete([Message.user("hi")], max_tokens=64)

    assert res.content == "ok"
    assert captured[0]["max_tokens"] == 64
    assert captured[1]["max_completion_tokens"] == 64
    assert "max_tokens" not in captured[1]
    # corrected flags stick for the session
    assert p._flags.max_tokens_field == "max_completion_tokens"


def test_cached_tokens_parsed_into_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
    }
    monkeypatch.setattr(compat.httpx, "post", _capture_post([_Resp(payload=payload)], []))
    p = OpenAICompatibleProvider(model="m", base_url="https://x/v1", api_key="k")
    res = p.complete([Message.user("hi")])
    assert res.usage["cache_read_tokens"] == 80
    assert res.usage["input_tokens"] == 100

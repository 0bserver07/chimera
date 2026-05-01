"""Tests for the vLLM / SGLang local-serving provider routing.

Both servers expose OpenAI-compatible ``/v1/chat/completions`` endpoints
on documented default ports (vLLM ``:8000``, SGLang ``:30000``). The
factory wraps them with :class:`chimera.providers.compatible.OpenAICompatibleProvider`
preconfigured against the right base URL and a ``noop`` default key.

These tests cover:

* Prefix-based provider inference (``vllm/<model>`` / ``sglang/<model>``).
* Factory routing — ``provider_type="vllm"`` / ``"sglang"`` strips the
  prefix and constructs an OpenAI-compatible provider.
* Env-var overrides (``$VLLM_BASE_URL``, ``$VLLM_API_KEY``,
  ``$SGLANG_BASE_URL``, ``$SGLANG_API_KEY``).
* Probe helpers (``probe_vllm`` / ``probe_sglang``) using a fake
  ``urllib.request.urlopen``.
* Per-CLI fallback wiring in shrew / weasel / otter / ferret.

We mock ``httpx`` (the OpenAI-compatible provider's transport) and
``urllib.request.urlopen`` (the probe transport) so no network traffic
occurs.
"""
from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ns(**overrides: Any) -> argparse.Namespace:
    """Build an argparse-style namespace with ``model=None`` by default."""
    base: dict[str, Any] = {"model": None, "no_color": False}
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every env var that influences provider resolution."""
    for var in (
        "VLLM_BASE_URL",
        "VLLM_API_KEY",
        "SGLANG_BASE_URL",
        "SGLANG_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENROUTER_API_KEY",
        "LLAMACPP_API_KEY",
        "LLAMACPP_BASE_URL",
        "OLLAMA_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
        "XAI_API_KEY",
        "SHREW_MODEL",
        "WEASEL_MODEL",
        "OTTER_MODEL",
        "FERRET_MODEL",
        "ANTHROPIC_MODEL",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Factory: prefix inference
# ---------------------------------------------------------------------------


def test_infer_provider_vllm_prefix(clear_env: None) -> None:
    """``vllm/<model>`` resolves to the ``vllm`` provider type."""
    from chimera.providers.factory import _infer_provider

    assert _infer_provider("vllm/qwen3.6-35b-a3b") == "vllm"
    # Mixed case still routes correctly.
    assert _infer_provider("VLLM/qwen3.6-35b-a3b") == "vllm"


def test_infer_provider_sglang_prefix(clear_env: None) -> None:
    """``sglang/<model>`` resolves to the ``sglang`` provider type."""
    from chimera.providers.factory import _infer_provider

    assert _infer_provider("sglang/qwen3.6-35b-a3b") == "sglang"
    assert _infer_provider("SGLang/qwen3.6-35b-a3b") == "sglang"


def test_infer_provider_vllm_sglang_beat_anthropic_env(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``vllm/`` / ``sglang/`` win over an ``$ANTHROPIC_BASE_URL`` override.

    Users who explicitly namespaced the model with ``vllm/`` shouldn't
    have it silently rewritten to ``anthropic`` because they happen to
    also have an Anthropic-compat base URL set.
    """
    from chimera.providers.factory import _infer_provider

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ollama")
    assert _infer_provider("vllm/qwen3.6-35b-a3b") == "vllm"
    assert _infer_provider("sglang/qwen3.6-35b-a3b") == "sglang"


# ---------------------------------------------------------------------------
# Factory: create_provider strips the prefix and routes through compatible
# ---------------------------------------------------------------------------


def test_create_provider_vllm_uses_default_base_url(
    clear_env: None,
) -> None:
    """``provider_type="vllm"`` lands on ``http://localhost:8000/v1``."""
    with patch("chimera.providers.compatible.httpx") as mock_httpx:
        mock_httpx.post = MagicMock()
        from chimera.providers.factory import create_provider

        provider = create_provider(
            provider_type="vllm", model="vllm/qwen3.6-35b-a3b",
        )
        # The ``vllm/`` prefix is stripped before reaching the server.
        assert provider.model_name == "qwen3.6-35b-a3b"
        assert provider._base_url == "http://localhost:8000/v1"
        # Default API key is "noop" — vLLM doesn't auth by default.
        assert provider._api_key == "noop"


def test_create_provider_sglang_uses_default_base_url(
    clear_env: None,
) -> None:
    """``provider_type="sglang"`` lands on ``http://localhost:30000/v1``."""
    with patch("chimera.providers.compatible.httpx"):
        from chimera.providers.factory import create_provider

        provider = create_provider(
            provider_type="sglang", model="sglang/qwen3.6-35b-a3b",
        )
        assert provider.model_name == "qwen3.6-35b-a3b"
        assert provider._base_url == "http://localhost:30000/v1"
        assert provider._api_key == "noop"


def test_create_provider_vllm_honors_env_overrides(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$VLLM_BASE_URL`` and ``$VLLM_API_KEY`` override the defaults."""
    monkeypatch.setenv("VLLM_BASE_URL", "http://10.0.0.5:8001/v1")
    monkeypatch.setenv("VLLM_API_KEY", "vllm-secret")

    with patch("chimera.providers.compatible.httpx"):
        from chimera.providers.factory import create_provider

        provider = create_provider(
            provider_type="vllm", model="vllm/llama-3.1-70b",
        )
        assert provider._base_url == "http://10.0.0.5:8001/v1"
        assert provider._api_key == "vllm-secret"
        assert provider.model_name == "llama-3.1-70b"


def test_create_provider_sglang_honors_env_overrides(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$SGLANG_BASE_URL`` and ``$SGLANG_API_KEY`` override the defaults."""
    monkeypatch.setenv("SGLANG_BASE_URL", "http://192.168.1.42:30001/v1")
    monkeypatch.setenv("SGLANG_API_KEY", "sgl-secret")

    with patch("chimera.providers.compatible.httpx"):
        from chimera.providers.factory import create_provider

        provider = create_provider(
            provider_type="sglang", model="sglang/llama-3.1-70b",
        )
        assert provider._base_url == "http://192.168.1.42:30001/v1"
        assert provider._api_key == "sgl-secret"
        assert provider.model_name == "llama-3.1-70b"


def test_create_provider_explicit_base_url_wins_over_env(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``base_url=`` kwarg beats the env var."""
    monkeypatch.setenv("VLLM_BASE_URL", "http://10.0.0.5:8001/v1")

    with patch("chimera.providers.compatible.httpx"):
        from chimera.providers.factory import create_provider

        provider = create_provider(
            provider_type="vllm",
            model="vllm/llama-3.1-70b",
            base_url="http://override.example/v1",
        )
        assert provider._base_url == "http://override.example/v1"


def test_create_provider_via_inference(clear_env: None) -> None:
    """``create_provider(model="vllm/...")`` infers the provider type."""
    with patch("chimera.providers.compatible.httpx"):
        from chimera.providers.factory import create_provider

        provider = create_provider(model="vllm/qwen3.6-35b-a3b")
        assert provider._base_url == "http://localhost:8000/v1"
        assert provider.model_name == "qwen3.6-35b-a3b"

        provider = create_provider(model="sglang/qwen3.6-35b-a3b")
        assert provider._base_url == "http://localhost:30000/v1"
        assert provider.model_name == "qwen3.6-35b-a3b"


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------


class _FakeResp:
    status = 200

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def getcode(self) -> int:
        return 200


def test_probe_vllm_hits_models_endpoint(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``probe_vllm`` issues a GET against ``$VLLM_BASE_URL/models``."""
    seen: list[str] = []

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResp:
        seen.append(req.full_url)
        return _FakeResp()

    monkeypatch.setattr(
        "chimera.providers.factory.urllib.request.urlopen", _fake_urlopen,
    )
    from chimera.providers.factory import probe_vllm

    assert probe_vllm() is True
    assert seen[0].endswith("/models")
    assert "8000" in seen[0]


def test_probe_sglang_hits_models_endpoint(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``probe_sglang`` issues a GET against ``$SGLANG_BASE_URL/models``."""
    seen: list[str] = []

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResp:
        seen.append(req.full_url)
        return _FakeResp()

    monkeypatch.setattr(
        "chimera.providers.factory.urllib.request.urlopen", _fake_urlopen,
    )
    from chimera.providers.factory import probe_sglang

    assert probe_sglang() is True
    assert seen[0].endswith("/models")
    assert "30000" in seen[0]


def test_probe_vllm_unreachable_returns_false(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection-refused exceptions are swallowed."""
    def _fake_urlopen(req: Any, timeout: float = 0.0) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(
        "chimera.providers.factory.urllib.request.urlopen", _fake_urlopen,
    )
    from chimera.providers.factory import probe_sglang, probe_vllm

    assert probe_vllm() is False
    assert probe_sglang() is False


def test_probe_vllm_accepts_auth_errors(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401/403 response still proves the server is alive."""
    import urllib.error

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> Any:
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    monkeypatch.setattr(
        "chimera.providers.factory.urllib.request.urlopen", _fake_urlopen,
    )
    from chimera.providers.factory import probe_vllm

    assert probe_vllm() is True


def test_probe_vllm_honors_explicit_base_url(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``base_url`` argument overrides ``$VLLM_BASE_URL``."""
    seen: list[str] = []

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResp:
        seen.append(req.full_url)
        return _FakeResp()

    monkeypatch.setattr(
        "chimera.providers.factory.urllib.request.urlopen", _fake_urlopen,
    )
    monkeypatch.setenv("VLLM_BASE_URL", "http://wrong.example/v1")
    from chimera.providers.factory import probe_vllm

    assert probe_vllm("http://right.example/v1") is True
    assert seen[0].startswith("http://right.example/v1")


# ---------------------------------------------------------------------------
# Per-CLI provider chains
# ---------------------------------------------------------------------------


@pytest.fixture
def capture_factory(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace ``chimera.providers.factory.create_provider`` with a stub."""
    record: dict[str, Any] = {"calls": []}

    def _stub(**kwargs: Any) -> Any:
        record["calls"].append(kwargs)
        stub = MagicMock()
        stub.kwargs = kwargs
        stub.model_name = kwargs.get("model", "")
        return stub

    monkeypatch.setattr(
        "chimera.providers.factory.create_provider", _stub,
    )
    return record


def test_shrew_routes_vllm_prefix(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--model vllm/<id>`` in shrew routes to the vllm factory branch."""
    # Force probes off so the resolved id stays as the explicit prefix.
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_llamacpp", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_vllm", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_sglang", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_ollama", lambda base_url=None: False,
    )
    from chimera.shrew import providers as shrew_providers

    shrew_providers.build_provider(_ns(model="vllm/qwen-7b"))
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "vllm"
    assert call["model"] == "vllm/qwen-7b"


def test_shrew_routes_sglang_prefix(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--model sglang/<id>`` in shrew routes to the sglang factory branch."""
    for fn in ("probe_llamacpp", "probe_vllm", "probe_sglang", "probe_ollama"):
        monkeypatch.setattr(
            f"chimera.shrew.providers.{fn}", lambda base_url=None: False,
        )
    from chimera.shrew import providers as shrew_providers

    shrew_providers.build_provider(_ns(model="sglang/llama-3.1-8b"))
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "sglang"
    assert call["model"] == "sglang/llama-3.1-8b"


def test_shrew_vllm_probe_picks_default(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reachable vLLM probe (no llama.cpp) selects the vLLM default."""
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_llamacpp", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_vllm", lambda base_url=None: True,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_sglang", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_ollama", lambda base_url=None: False,
    )
    from chimera.shrew import providers as shrew_providers

    shrew_providers.build_provider(_ns())
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "vllm"
    # The default model carries the ``vllm/`` prefix (factory strips it).
    assert call["model"].startswith("vllm/")


def test_shrew_sglang_probe_picks_default(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reachable SGLang probe (no llama.cpp / no vLLM) selects SGLang."""
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_llamacpp", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_vllm", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_sglang", lambda base_url=None: True,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_ollama", lambda base_url=None: False,
    )
    from chimera.shrew import providers as shrew_providers

    shrew_providers.build_provider(_ns())
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "sglang"
    assert call["model"].startswith("sglang/")


def test_shrew_llamacpp_probe_still_wins_over_vllm(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BOTH llama.cpp and vLLM are up, llama.cpp wins (chain order)."""
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_llamacpp", lambda base_url=None: True,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_vllm", lambda base_url=None: True,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_sglang", lambda base_url=None: True,
    )
    monkeypatch.setattr(
        "chimera.shrew.providers.probe_ollama", lambda base_url=None: True,
    )
    from chimera.shrew import providers as shrew_providers

    shrew_providers.build_provider(_ns())
    call = capture_factory["calls"][0]
    # llama.cpp branch routes through ``compatible``, not ``vllm``.
    assert call["provider_type"] == "compatible"
    assert call["model"] == shrew_providers._DEFAULT_LLAMACPP_MODEL


def test_weasel_routes_vllm_prefix(
    clear_env: None,
    capture_factory: dict[str, Any],
) -> None:
    """``--model vllm/<id>`` in weasel routes to the vllm factory branch."""
    from chimera.weasel import providers as weasel_providers

    weasel_providers.build_provider(_ns(model="vllm/qwen-7b"))
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "vllm"
    assert call["model"] == "vllm/qwen-7b"


def test_weasel_routes_sglang_prefix(
    clear_env: None,
    capture_factory: dict[str, Any],
) -> None:
    """``--model sglang/<id>`` in weasel routes to the sglang factory branch."""
    from chimera.weasel import providers as weasel_providers

    weasel_providers.build_provider(_ns(model="sglang/llama-3.1-8b"))
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "sglang"


def test_weasel_vllm_api_key_fallback(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$VLLM_API_KEY`` + reachable probe -> vLLM default (no cloud key)."""
    monkeypatch.setenv("VLLM_API_KEY", "vllm-token")
    monkeypatch.setattr(
        "chimera.providers.factory.probe_vllm", lambda base_url=None: True,
    )
    monkeypatch.setattr(
        "chimera.providers.factory.probe_sglang", lambda base_url=None: False,
    )
    from chimera.weasel import providers as weasel_providers

    weasel_providers.build_provider(_ns())
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "vllm"
    assert call["model"].startswith("vllm/")


def test_weasel_sglang_api_key_fallback(
    clear_env: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$SGLANG_API_KEY`` + reachable probe -> SGLang default."""
    monkeypatch.setenv("SGLANG_API_KEY", "sgl-token")
    monkeypatch.setattr(
        "chimera.providers.factory.probe_vllm", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        "chimera.providers.factory.probe_sglang", lambda base_url=None: True,
    )
    from chimera.weasel import providers as weasel_providers

    weasel_providers.build_provider(_ns())
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "sglang"
    assert call["model"].startswith("sglang/")


def test_weasel_vllm_api_key_without_probe_skipped(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$VLLM_API_KEY`` set but probe fails -> friendly error."""
    monkeypatch.setenv("VLLM_API_KEY", "vllm-token")
    monkeypatch.setattr(
        "chimera.providers.factory.probe_vllm", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        "chimera.providers.factory.probe_sglang", lambda base_url=None: False,
    )
    from chimera.weasel import providers as weasel_providers

    with pytest.raises(ValueError, match="weasel: no provider configured"):
        weasel_providers.build_provider(_ns())


def test_otter_routes_vllm_prefix(
    clear_env: None,
    capture_factory: dict[str, Any],
) -> None:
    """``--model vllm/<id>`` in otter routes to the vllm factory branch."""
    from chimera.otter import providers as otter_providers

    otter_providers.build_provider(_ns(model="vllm/qwen-7b"))
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "vllm"


def test_otter_routes_sglang_prefix(
    clear_env: None,
    capture_factory: dict[str, Any],
) -> None:
    """``--model sglang/<id>`` in otter routes to the sglang factory branch."""
    from chimera.otter import providers as otter_providers

    otter_providers.build_provider(_ns(model="sglang/llama-3.1-8b"))
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "sglang"


def test_ferret_routes_vllm_prefix(
    clear_env: None,
    capture_factory: dict[str, Any],
) -> None:
    """``--model vllm/<id>`` in ferret routes to the vllm factory branch."""
    from chimera.ferret import providers as ferret_providers

    ferret_providers.build_provider(_ns(model="vllm/qwen-7b"))
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "vllm"


def test_ferret_routes_sglang_prefix(
    clear_env: None,
    capture_factory: dict[str, Any],
) -> None:
    """``--model sglang/<id>`` in ferret routes to the sglang factory branch."""
    from chimera.ferret import providers as ferret_providers

    ferret_providers.build_provider(_ns(model="sglang/llama-3.1-8b"))
    call = capture_factory["calls"][0]
    assert call["provider_type"] == "sglang"


# ---------------------------------------------------------------------------
# Catalog visibility
# ---------------------------------------------------------------------------


def test_shrew_catalog_lists_vllm_and_sglang() -> None:
    """``chimera shrew --list-models`` advertises both new servers."""
    from chimera.shrew.providers import format_catalog

    text = format_catalog()
    assert "vllm" in text
    assert "sglang" in text
    assert "localhost:8000" in text
    assert "localhost:30000" in text


def test_weasel_catalog_lists_vllm_and_sglang() -> None:
    """``chimera weasel --list-models`` advertises both new servers."""
    from chimera.weasel.providers import format_catalog

    text = format_catalog()
    assert "VLLM_API_KEY" in text
    assert "SGLANG_API_KEY" in text


def test_weasel_no_key_message_lists_vllm_and_sglang(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The friendly error names ``$VLLM_API_KEY`` / ``$SGLANG_API_KEY``."""
    monkeypatch.setattr(
        "chimera.providers.factory.probe_vllm", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        "chimera.providers.factory.probe_sglang", lambda base_url=None: False,
    )
    from chimera.weasel import providers as weasel_providers

    with pytest.raises(ValueError) as exc:
        weasel_providers.build_provider(_ns())
    msg = str(exc.value)
    assert "VLLM_API_KEY" in msg
    assert "SGLANG_API_KEY" in msg
    assert "localhost:8000" in msg
    assert "localhost:30000" in msg

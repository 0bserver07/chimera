"""Tests for ``chimera.shrew.providers`` (agent S5).

Covers the env-var- and probe-controlled provider chain:

1. Explicit ``args.model`` wins over everything.
2. ``$SHREW_MODEL`` wins over the probe / env-var chain.
3. A reachable llama.cpp probe -> default ``qwen3.6-35b-a3b`` via the
   OpenAI-compatible provider against ``$LLAMACPP_BASE_URL``.
4. No llama.cpp + reachable Ollama probe -> default ``qwen3.5:cloud``
   via the OpenAI-compatible provider against ``$OLLAMA_BASE_URL/v1``.
5. No probes + ``$ANTHROPIC_API_KEY`` -> Anthropic default.
6. No probes + ``$OPENAI_API_KEY`` -> OpenAI default.
7. No probes + ``$OPENROUTER_API_KEY`` -> ``compatible`` against
   ``openrouter.ai``.
8. Friendly :class:`ValueError` when nothing is configured.

Plus:

* Catalog metadata (``context_window`` / ``max_output_tokens`` / ``moe``).
* ``--list-models`` / :func:`format_catalog` output exposes every chain
  step.
* ``$LLAMACPP_BASE_URL`` / ``$OLLAMA_BASE_URL`` overrides flow through.

Tests stub :func:`chimera.providers.factory.create_provider` and the HTTP
probe helpers so we never hit real SDKs / network. Each test isolates env
vars via ``monkeypatch``.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from chimera.shrew import providers as shrew_providers


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _StubProvider:
    """Minimal stand-in for :class:`chimera.providers.base.Provider`."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.model_name = kwargs.get("model", "")


def _ns(**overrides: Any) -> argparse.Namespace:
    """Build a default-ish shrew argparse namespace for tests.

    We default ``model=None`` so the chain is the path under test;
    callers override per-test.
    """
    base: dict[str, Any] = {"model": None, "no_color": False}
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every shrew-relevant env var so tests start clean."""
    for var in (
        "SHREW_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "LLAMACPP_API_KEY",
        "LLAMACPP_BASE_URL",
        "OLLAMA_API_KEY",
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def capture_factory(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace :func:`create_provider` with a capturing stub."""
    record: dict[str, Any] = {"calls": []}

    def _stub(**kwargs: Any) -> _StubProvider:
        record["calls"].append(kwargs)
        return _StubProvider(**kwargs)

    monkeypatch.setattr(
        "chimera.providers.factory.create_provider",
        _stub,
    )
    return record


@pytest.fixture
def no_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every probe to return False (simulates no local server)."""
    monkeypatch.setattr(
        shrew_providers, "probe_llamacpp", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        shrew_providers, "probe_ollama", lambda base_url=None: False,
    )


@pytest.fixture
def llamacpp_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe says llama.cpp is up; Ollama is down."""
    monkeypatch.setattr(
        shrew_providers, "probe_llamacpp", lambda base_url=None: True,
    )
    monkeypatch.setattr(
        shrew_providers, "probe_ollama", lambda base_url=None: False,
    )


@pytest.fixture
def ollama_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe says Ollama is up; llama.cpp is down."""
    monkeypatch.setattr(
        shrew_providers, "probe_llamacpp", lambda base_url=None: False,
    )
    monkeypatch.setattr(
        shrew_providers, "probe_ollama", lambda base_url=None: True,
    )


# ---------------------------------------------------------------------------
# Resolution chain — happy paths
# ---------------------------------------------------------------------------


def test_explicit_model_wins(
    clear_env: None,
    no_probes: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``args.model`` overrides every env var and probe."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    monkeypatch.setenv("SHREW_MODEL", "qwen3.5-9b")

    shrew_providers.build_provider(_ns(model="gpt-4o"))

    call = capture_factory["calls"][0]
    assert call["model"] == "gpt-4o"


def test_shrew_model_env_wins_over_probes(
    clear_env: None,
    llamacpp_up: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$SHREW_MODEL`` wins over a reachable llama.cpp probe."""
    monkeypatch.setenv("SHREW_MODEL", "claude-sonnet-4-6")

    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["model"] == "claude-sonnet-4-6"
    # SHREW_MODEL=claude-sonnet-4-6 is a cloud model, so it should NOT
    # be routed through the compatible provider.
    assert call.get("provider_type") is None


def test_llamacpp_probe_picks_default(
    clear_env: None,
    llamacpp_up: None,
    capture_factory: dict[str, Any],
) -> None:
    """A reachable llama.cpp probe selects ``qwen3.6-35b-a3b``."""
    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "qwen3.6-35b-a3b"
    assert call["base_url"] == "http://127.0.0.1:8888/v1"
    # Catalog says 32_768 for qwen3.6-35b-a3b.
    assert call["context_length"] == 32_768


def test_llamacpp_base_url_override(
    clear_env: None,
    llamacpp_up: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$LLAMACPP_BASE_URL`` overrides the default 127.0.0.1:8888."""
    monkeypatch.setenv("LLAMACPP_BASE_URL", "http://10.0.0.5:7777/v1")

    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["base_url"] == "http://10.0.0.5:7777/v1"


def test_llamacpp_api_key_forwarded(
    clear_env: None,
    llamacpp_up: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$LLAMACPP_API_KEY`` is forwarded to the compatible provider."""
    monkeypatch.setenv("LLAMACPP_API_KEY", "lc-secret")

    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["api_key"] == "lc-secret"


def test_explicit_qwen_9b_routes_to_llamacpp(
    clear_env: None,
    no_probes: None,  # even with no probes, catalog membership routes us
    capture_factory: dict[str, Any],
) -> None:
    """An explicit ``qwen3.5-9b`` routes through llama.cpp via catalog."""
    shrew_providers.build_provider(_ns(model="qwen3.5-9b"))

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "qwen3.5-9b"
    assert call["base_url"] == "http://127.0.0.1:8888/v1"


def test_ollama_probe_picks_default(
    clear_env: None,
    ollama_up: None,
    capture_factory: dict[str, Any],
) -> None:
    """No llama.cpp, but Ollama up -> default ``qwen3.5:cloud`` route."""
    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "qwen3.5:cloud"
    assert call["base_url"] == "http://localhost:11434/v1"
    # Catalog says 262_144 for the cloud tag.
    assert call["context_length"] == 262_144


def test_ollama_base_url_override(
    clear_env: None,
    ollama_up: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$OLLAMA_BASE_URL`` overrides the default localhost:11434."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.42:11434")

    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["base_url"] == "http://192.168.1.42:11434/v1"


def test_ollama_tag_detection_routes_compatible(
    clear_env: None,
    no_probes: None,
    capture_factory: dict[str, Any],
) -> None:
    """``--model llama3.2:3b`` always routes via the compatible shim."""
    shrew_providers.build_provider(_ns(model="llama3.2:3b"))

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "llama3.2:3b"
    # Tag isn't in catalog -> default 32_768 context.
    assert call["context_length"] == 32_768
    # Goes to Ollama (not llama.cpp) because of the colon shape.
    assert call["base_url"] == "http://localhost:11434/v1"


def test_anthropic_fallback_when_no_probes(
    clear_env: None,
    no_probes: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No local servers + ``$ANTHROPIC_API_KEY`` -> Anthropic default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["model"] == "claude-sonnet-4-6"
    # Should be the regular factory path, not compatible.
    assert call.get("provider_type") is None


def test_openai_fallback_when_no_probes(
    clear_env: None,
    no_probes: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No local servers + ``$OPENAI_API_KEY`` -> OpenAI default."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["model"] == "gpt-4o"
    assert call.get("provider_type") is None


def test_anthropic_priority_over_openai(
    clear_env: None,
    no_probes: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both cloud keys + no probes -> Anthropic wins (per chain order).

    Documents shrew's chain order, which differs from weasel: shrew
    prefers Anthropic at the cloud layer because small-model users who
    reach for a cloud key tend to want Claude for the harder tasks.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")

    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["model"] == "claude-sonnet-4-6"


def test_openrouter_fallback_when_no_probes(
    clear_env: None,
    no_probes: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No local servers + only ``$OPENROUTER_API_KEY`` -> compatible route."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")

    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "openai/gpt-4o"
    assert call["api_key"] == "sk-or-xxx"
    assert call["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_routes_when_model_has_slash(
    clear_env: None,
    no_probes: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``$OPENROUTER_API_KEY`` + slash model -> compatible route."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xxx")

    shrew_providers.build_provider(_ns(model="anthropic/claude-sonnet-4"))

    call = capture_factory["calls"][0]
    assert call["provider_type"] == "compatible"
    assert call["model"] == "anthropic/claude-sonnet-4"
    assert call["base_url"] == "https://openrouter.ai/api/v1"


def test_local_probes_take_priority_over_cloud_keys(
    clear_env: None,
    llamacpp_up: None,
    capture_factory: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reachable local server beats a stray cloud key (small-model-first)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")

    shrew_providers.build_provider(_ns())

    call = capture_factory["calls"][0]
    assert call["model"] == "qwen3.6-35b-a3b"
    assert call["provider_type"] == "compatible"


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_no_keys_no_probes_raises_friendly_error(
    clear_env: None,
    no_probes: None,
) -> None:
    """No model + no env vars + no probes -> :class:`ValueError`."""
    with pytest.raises(ValueError) as exc_info:
        shrew_providers.build_provider(_ns())

    msg = str(exc_info.value)
    for needle in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "LLAMACPP_BASE_URL",
        "OLLAMA_BASE_URL",
        "SHREW_MODEL",
        "qwen3.6-35b-a3b",
        "qwen3.5:cloud",
    ):
        assert needle in msg, f"friendly error missing {needle!r}"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_get_catalog_shape() -> None:
    """The catalog records context window + max output + MoE flag."""
    catalog = shrew_providers.get_catalog()

    # Default model must be in the catalog.
    assert "qwen3.6-35b-a3b" in catalog

    entry = catalog["qwen3.6-35b-a3b"]
    assert entry["context_window"] == 32_768
    assert entry["max_output_tokens"] == 4_096
    assert entry["moe"] is True
    assert entry["backend"] == "llamacpp"

    # The smaller dense model is also catalogued.
    nine_b = catalog["qwen3.5-9b"]
    assert nine_b["moe"] is False
    assert nine_b["backend"] == "llamacpp"

    # Ollama tag default is catalogued and tagged accordingly.
    cloud = catalog["qwen3.5:cloud"]
    assert cloud["context_window"] == 262_144
    assert cloud["backend"] == "ollama"


def test_get_catalog_returns_copy() -> None:
    """Mutating the returned dict must not affect the module catalog."""
    catalog = shrew_providers.get_catalog()
    catalog["bogus"] = {"context_window": 1}

    again = shrew_providers.get_catalog()
    assert "bogus" not in again


def test_resolved_catalog_shape() -> None:
    """The catalog enumerates every chain step in order."""
    catalog = shrew_providers.resolved_catalog()
    sources = [src for _model, src in catalog]
    # Order-significant: mirrors the resolution chain.
    assert "llama.cpp" in sources[0]
    assert "vllm" in sources[1]
    assert "sglang" in sources[2]
    assert "ollama" in sources[3]
    assert sources[4] == "ANTHROPIC_API_KEY"
    assert sources[5] == "OPENAI_API_KEY"
    assert sources[6] == "OPENROUTER_API_KEY"
    # XAI is appended last by the linter-applied chain extension.
    assert sources[-1] == "XAI_API_KEY"
    assert len(catalog) == 8


def test_format_catalog_contains_every_default() -> None:
    """The textual format names every default model id."""
    text = shrew_providers.format_catalog()
    for needle in (
        "qwen3.6-35b-a3b",
        "qwen3.5:cloud",
        "claude-sonnet-4-6",
        "gpt-4o",
        "openai/gpt-4o",
        "127.0.0.1:8888",
        "localhost:11434",
    ):
        assert needle in text, f"catalog missing {needle!r}"


# ---------------------------------------------------------------------------
# Probe helpers (using mocked urlopen)
# ---------------------------------------------------------------------------


def test_probe_llamacpp_health_succeeds(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``probe_llamacpp`` returns True when ``/health`` answers 200."""
    seen: list[str] = []

    class _FakeResp:
        status = 200

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def getcode(self) -> int:
            return 200

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResp:
        seen.append(req.full_url)
        return _FakeResp()

    monkeypatch.setattr(
        "chimera.shrew.providers.urllib.request.urlopen", _fake_urlopen,
    )

    assert shrew_providers.probe_llamacpp() is True
    # We hit /health first.
    assert seen[0].endswith("/health")


def test_probe_llamacpp_falls_back_to_models(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/health`` 404 -> probe falls back to ``/v1/models``."""
    seen: list[str] = []

    class _FakeResp:
        status = 200

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def getcode(self) -> int:
            return 200

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResp:
        seen.append(req.full_url)
        if req.full_url.endswith("/health"):
            raise urllib_error_404()
        return _FakeResp()

    monkeypatch.setattr(
        "chimera.shrew.providers.urllib.request.urlopen", _fake_urlopen,
    )

    assert shrew_providers.probe_llamacpp() is True
    assert any(url.endswith("/models") for url in seen)


def test_probe_llamacpp_unreachable(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection refused -> ``probe_llamacpp`` returns False."""
    def _fake_urlopen(req: Any, timeout: float = 0.0) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(
        "chimera.shrew.providers.urllib.request.urlopen", _fake_urlopen,
    )

    assert shrew_providers.probe_llamacpp() is False


def test_probe_ollama_uses_api_tags(
    clear_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``probe_ollama`` hits ``/api/tags`` on the configured base URL."""
    seen: list[str] = []

    class _FakeResp:
        status = 200

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def getcode(self) -> int:
            return 200

    def _fake_urlopen(req: Any, timeout: float = 0.0) -> _FakeResp:
        seen.append(req.full_url)
        return _FakeResp()

    monkeypatch.setattr(
        "chimera.shrew.providers.urllib.request.urlopen", _fake_urlopen,
    )

    assert shrew_providers.probe_ollama() is True
    assert seen[0].endswith("/api/tags")
    assert "11434" in seen[0]


# ---------------------------------------------------------------------------
# Helpers used in probe tests
# ---------------------------------------------------------------------------


def urllib_error_404() -> Exception:
    """Build a 404 :class:`urllib.error.HTTPError` for probe-fallback tests."""
    import urllib.error

    return urllib.error.HTTPError(
        url="http://127.0.0.1:8888/health",
        code=404,
        msg="Not Found",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )


# ---------------------------------------------------------------------------
# Returned provider sanity check
# ---------------------------------------------------------------------------


def test_returned_object_is_factory_output(
    clear_env: None,
    llamacpp_up: None,
    capture_factory: dict[str, Any],
) -> None:
    """``build_provider`` returns whatever the factory returns."""
    result = shrew_providers.build_provider(_ns())

    assert isinstance(result, _StubProvider)
    assert result.model_name == "qwen3.6-35b-a3b"

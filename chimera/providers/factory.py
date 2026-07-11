"""Factory for creating LLM provider instances.

Provides :func:`create_provider`, which instantiates the correct
:class:`~chimera.providers.base.Provider` subclass based on an explicit
provider type or by inferring it from the model name.

Example:
    ```python
    from chimera.providers.factory import create_provider

    provider = create_provider(model="claude-sonnet-4-20250514")
    ```
"""

# chimera/providers/factory.py
from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from chimera.providers.base import Provider

if TYPE_CHECKING:
    from chimera.auth.manager import AuthManager

# Per-probe socket timeout for local-server liveness checks. Kept tight so
# resolution chains feel snappy when the server isn't running.
_LOCAL_PROBE_TIMEOUT_SECONDS = 0.25


def _local_probe(url: str, timeout: float = _LOCAL_PROBE_TIMEOUT_SECONDS) -> bool:
    """Return ``True`` when an HTTP GET to *url* yields a non-error status.

    Mirrors :func:`chimera.shrew.providers._http_probe`: stdlib-only
    (:mod:`urllib.request`), with a tight socket timeout so callers can
    chain probes without paying for stale base URLs. ``401``/``403`` are
    treated as "alive" because the OpenAI-compat ``/v1/models`` endpoint
    may demand a key even when the caller just wants a liveness signal.
    """
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = getattr(resp, "status", None) or resp.getcode()
            return 200 <= int(status) < 400
    except urllib.error.HTTPError as err:
        return err.code in (401, 403)
    except Exception:  # noqa: BLE001 — any failure means "unreachable"
        return False


def vllm_base_url() -> str:
    """Return the configured vLLM base URL.

    Reads ``$VLLM_BASE_URL`` and falls back to the documented vLLM default
    (``http://localhost:8000/v1``). Trailing slashes are trimmed so callers
    can safely append paths.
    """
    return os.environ.get("VLLM_BASE_URL", _VLLM_DEFAULT_BASE_URL).rstrip("/")


def sglang_base_url() -> str:
    """Return the configured SGLang base URL.

    Reads ``$SGLANG_BASE_URL`` and falls back to the documented SGLang
    default (``http://localhost:30000/v1``). Trailing slashes are trimmed.
    """
    return os.environ.get(
        "SGLANG_BASE_URL", _SGLANG_DEFAULT_BASE_URL,
    ).rstrip("/")


def probe_vllm(base_url: str | None = None) -> bool:
    """Return ``True`` when a vLLM server answers at *base_url*.

    Probes ``/models`` (the OpenAI-compatible listing endpoint, which vLLM
    serves under its ``/v1`` mount).

    Args:
        base_url: Override ``$VLLM_BASE_URL``. When ``None`` we resolve
            from env (with a default of :data:`_VLLM_DEFAULT_BASE_URL`).
    """
    url = (base_url or vllm_base_url()).rstrip("/")
    return _local_probe(f"{url}/models")


def probe_sglang(base_url: str | None = None) -> bool:
    """Return ``True`` when an SGLang server answers at *base_url*.

    Probes ``/models``. SGLang's OpenAI-compatible router lives under its
    ``/v1`` mount on port 30000 by default.

    Args:
        base_url: Override ``$SGLANG_BASE_URL``. When ``None`` we resolve
            from env (with a default of :data:`_SGLANG_DEFAULT_BASE_URL`).
    """
    url = (base_url or sglang_base_url()).rstrip("/")
    return _local_probe(f"{url}/models")

# Maps provider_type to auth provider name for token lookup.
_AUTH_PROVIDER_MAP: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "ollama": "ollama",
    "compatible": "openai",
    "modal": "modal",
    "vllm": "vllm",
    "sglang": "sglang",
    "xai": "xai",
}

# Local OpenAI-compatible serving defaults. Both vLLM and SGLang expose
# ``/v1/chat/completions`` so they piggy-back on
# :class:`~chimera.providers.compatible.OpenAICompatibleProvider`.
_VLLM_DEFAULT_BASE_URL = "http://localhost:8000/v1"
_SGLANG_DEFAULT_BASE_URL = "http://localhost:30000/v1"


def create_provider(
    provider_type: str | None = None,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    auth_manager: AuthManager | None = None,
    **kwargs: Any,
) -> Provider:
    """Factory function to create a provider by type or by model name inference.

    Args:
        provider_type: One of ``"anthropic"``, ``"openai"``, ``"google"``,
            ``"ollama"``, ``"compatible"``, ``"modal"``.  If ``None``, the
            type is inferred from *model*.
        model: Model identifier (e.g. ``"claude-sonnet-4-20250514"``,
            ``"gpt-4o"``, ``"gemini-2.0-flash"``).
        api_key: API key for the provider.  Falls back to the relevant
            environment variable when ``None``.
        base_url: Base URL override (primarily for ``"compatible"`` and
            ``"ollama"`` providers).
        auth_manager: Optional :class:`~chimera.auth.manager.AuthManager`
            instance.  When provided and *api_key* is ``None``, the factory
            tries ``auth_manager.get_token(provider_name)`` before falling
            back to environment variables.
        **kwargs: Additional keyword arguments forwarded to the provider
            constructor.

    Returns:
        A fully initialised :class:`~chimera.providers.base.Provider`
        instance ready to receive :meth:`~chimera.providers.base.Provider.complete`
        calls.

    Raises:
        ValueError: If *provider_type* is unknown or cannot be inferred from
            the model name.
    """
    from chimera.providers.registry import (
        _ensure_builtins_registered,
        get_provider_factory,
        list_providers,
    )
    _ensure_builtins_registered()

    if model is None:
        model = os.environ.get("ANTHROPIC_MODEL") or os.environ.get("OPENAI_MODEL")
        if model is None:
            raise ValueError(
                "No model specified. Either pass model=<name> or set one of:\n"
                "  - ANTHROPIC_API_KEY + ANTHROPIC_MODEL (Anthropic)\n"
                "  - OPENAI_API_KEY + OPENAI_MODEL (OpenAI)\n"
                "  - ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + ANTHROPIC_MODEL "
                "(Anthropic-compatible, e.g. GLM-5 via z.ai)"
            )

    if provider_type is None:
        provider_type = _infer_provider(model)

    # Strip ``vllm/``, ``sglang/``, and ``modal-endpoint/`` model-id
    # prefixes — they are hints used by ``_infer_provider`` to pick the
    # right serving stack, not part of the model name the server itself
    # sees. (For ``modal-endpoint/zai-org/GLM-5.2-FP8`` the tail keeps its
    # own ``/`` — Hugging Face repo ids are ``org/name``.)
    if provider_type in ("vllm", "sglang", "modal-endpoint") and "/" in model:
        head, _, tail = model.partition("/")
        if head.lower() == provider_type:
            model = tail

    # Try auth_manager for API key when none was explicitly provided.
    if api_key is None and auth_manager is not None:
        auth_name = _AUTH_PROVIDER_MAP.get(provider_type, provider_type)
        try:
            api_key = auth_manager.get_token(auth_name)
        except Exception:
            pass  # Fall through to env var lookup inside provider

    # vLLM and SGLang both expose OpenAI-compatible endpoints; route them
    # through the ``compatible`` provider with defaults pinned to each
    # server's documented port. ``$VLLM_BASE_URL`` / ``$SGLANG_BASE_URL``
    # override the URL; ``$VLLM_API_KEY`` / ``$SGLANG_API_KEY`` override
    # the (typically anonymous) key — local servers usually accept any
    # value, so we default to ``"noop"`` when neither is configured.
    if provider_type in ("vllm", "sglang"):
        if provider_type == "vllm":
            resolved_base = (
                base_url
                or os.environ.get("VLLM_BASE_URL")
                or _VLLM_DEFAULT_BASE_URL
            )
            resolved_key = (
                api_key
                or os.environ.get("VLLM_API_KEY")
                or "noop"
            )
        else:
            resolved_base = (
                base_url
                or os.environ.get("SGLANG_BASE_URL")
                or _SGLANG_DEFAULT_BASE_URL
            )
            resolved_key = (
                api_key
                or os.environ.get("SGLANG_API_KEY")
                or "noop"
            )
        compatible_factory = get_provider_factory("compatible")
        if compatible_factory is None:  # pragma: no cover — registry bug
            raise ValueError("compatible provider not registered")
        return compatible_factory(
            model=model,
            api_key=resolved_key,
            base_url=resolved_base,
            **kwargs,
        )

    factory = get_provider_factory(provider_type)
    if factory is not None:
        return factory(model=model, api_key=api_key, base_url=base_url, **kwargs)

    raise ValueError(
        f"Unknown provider: '{provider_type}'. "
        f"Registered: {list_providers()}"
    )


def _infer_provider(model: str) -> str:
    """Infer provider type from model name or environment.

    Inference order:

    1. **Env-var override.** If the user has set ``ANTHROPIC_BASE_URL`` or
       ``ANTHROPIC_AUTH_TOKEN`` they are explicitly pointing at an
       Anthropic-compatible endpoint (api.z.ai for GLM, Ollama's
       ``http://localhost:11434`` Anthropic-compat endpoint, Moonshot, etc.).
       Respect that intent and route everything through the ``anthropic``
       provider — UNLESS the model name clearly belongs to a different
       provider family (e.g. ``gpt-*``, ``o1/o3-*``, ``gemini-*``), in which
       case prefix wins to avoid routing OpenAI/Google calls into Anthropic.
    2. **Prefix-based inference.** Match known model-name prefixes.
    3. **Catalog fallback.** Look up the model in the default catalog.
    4. **Loose env fallback.** If only ``OPENAI_API_KEY`` is set, assume
       OpenAI.
    5. **Give up** with an actionable error message.
    """
    model_lower = model.lower()

    # Serving-stack prefixes win unconditionally — the user explicitly
    # opted into vLLM / SGLang / Modal managed endpoints by namespacing
    # the model id. In particular ``modal-endpoint/zai-org/GLM-5.2-FP8``
    # must NOT fall through to the ``glm`` → anthropic branch below.
    if model_lower.startswith("vllm/"):
        return "vllm"
    if model_lower.startswith("sglang/"):
        return "sglang"
    if model_lower.startswith("modal-endpoint/"):
        return "modal-endpoint"

    # Models that must NEVER be routed to the anthropic provider regardless of
    # env vars — they use fundamentally different wire protocols.
    _not_anthropic_prefixes = ("gpt", "o1", "o3", "codex", "gemini")

    anthropic_env_set = bool(
        os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    )

    # 1. Env-var override: user explicitly configured Anthropic-compat. Trust it.
    #    This fixes the Ollama-Anthropic-compat case where "qwen3.5:cloud" or
    #    "kimi-k2.6:cloud" should hit http://localhost:11434 via the anthropic
    #    provider, not Ollama's native /api/chat endpoint.
    # The deterministic test provider must never be hijacked by env overrides
    # (ANTHROPIC_BASE_URL is routinely set) — resolve it before everything.
    if model_lower.startswith("faux"):
        return "faux"

    if anthropic_env_set and not model_lower.startswith(_not_anthropic_prefixes):
        return "anthropic"

    # 2. Prefix-based inference.
    if model_lower.startswith("claude"):
        return "anthropic"
    # ``gpt-oss-*`` are OpenAI's open-weight models distributed via the
    # local Ollama daemon (and Ollama's cloud passthrough). Match them
    # BEFORE the generic ``gpt`` prefix so they don't get misrouted to
    # OpenAI's hosted API, which doesn't serve the OSS line.
    if model_lower.startswith("gpt-oss"):
        return "ollama"
    if model_lower.startswith(("gpt", "o1", "o3", "codex")):
        return "openai"
    if model_lower.startswith("gemini"):
        return "google"
    if model_lower.startswith("glm"):
        # GLM (e.g. GLM-5 via api.z.ai) uses the Anthropic-compatible wire
        # protocol. Users configure the endpoint via ANTHROPIC_BASE_URL /
        # ANTHROPIC_AUTH_TOKEN, which AnthropicProvider honors automatically.
        return "anthropic"
    if model_lower.startswith(("kimi", "moonshot")):
        # Kimi / Moonshot models are served via Anthropic-compatible endpoints
        # (api.moonshot.ai, Ollama cloud, etc.). Default to anthropic.
        return "anthropic"
    if model_lower.startswith("deepseek"):
        # DeepSeek-V4 family. ``:cloud``-tagged ids (e.g. ``deepseek-v4-pro:cloud``)
        # are served via the local Ollama daemon's cloud passthrough; bare ids
        # (``deepseek-v4``, ``deepseek-v4-pro``, ``deepseek-chat``,
        # ``deepseek-reasoner``) speak DeepSeek's hosted OpenAI-compatible API
        # at ``https://api.deepseek.com/v1`` and route through ``compatible``.
        # Catalog entries (``chimera.providers.catalog``) carry the per-model
        # base_url + api_key_env binding for the non-cloud variants.
        if model_lower.endswith(":cloud"):
            return "ollama"
        return "compatible"
    if model_lower.startswith("grok"):
        # xAI Grok models are served via the OpenAI-compatible API at
        # ``https://api.x.ai/v1``. The xai provider wraps
        # OpenAICompatibleProvider with that base URL and ``$XAI_API_KEY``.
        return "xai"
    if model_lower.startswith(("llama", "mistral", "qwen", "phi", "gemma")):
        # ``gemma`` covers Google's open-weight Gemma family (gemma3-27b
        # etc.) which is distributed through Ollama. Hosted Gemini stays
        # on the ``gemini`` branch above.
        return "ollama"

    # 3. Catalog fallback: check if model is in default catalog.
    from chimera.providers.catalog import ProviderCatalog
    catalog = ProviderCatalog.default()
    config = catalog.get(model)
    if config is not None:
        return config.provider_type

    # 4. Loose env fallback (anthropic case already handled above in step 1).
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"

    raise ValueError(
        f"Cannot infer provider from model name '{model}'.\n"
        f"Options:\n"
        f"  1. Anthropic-compatible endpoint (GLM-5 via z.ai, Ollama's\n"
        f"     Anthropic-compat API, Moonshot, etc.):\n"
        f"       export ANTHROPIC_BASE_URL='http://localhost:11434'\n"
        f"       export ANTHROPIC_AUTH_TOKEN='your-token'\n"
        f"     (for Ollama local, ANTHROPIC_AUTH_TOKEN=ollama works)\n"
        f"  2. Pass provider_type='anthropic' | 'openai' | 'google' | "
        f"'ollama' explicitly.\n"
        f"  3. Use a prefix that matches a known provider (claude-*, gpt-*,\n"
        f"     gpt-oss-* (Ollama), gemini-*, gemma* (Ollama), glm-*,\n"
        f"     kimi-*, grok-*, deepseek-*, llama*, qwen*, mistral*, phi*,\n"
        f"     vllm/* (local vLLM), sglang/* (local SGLang),\n"
        f"     modal-endpoint/* (Modal managed endpoints))."
    )

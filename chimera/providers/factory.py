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

from typing import TYPE_CHECKING, Any

from chimera.providers.base import Provider

if TYPE_CHECKING:
    from chimera.auth.manager import AuthManager

# Maps provider_type to auth provider name for token lookup.
_AUTH_PROVIDER_MAP: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "ollama": "ollama",
    "compatible": "openai",
    "modal": "modal",
}


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
        import os
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

    # Try auth_manager for API key when none was explicitly provided.
    if api_key is None and auth_manager is not None:
        auth_name = _AUTH_PROVIDER_MAP.get(provider_type, provider_type)
        try:
            api_key = auth_manager.get_token(auth_name)
        except Exception:
            pass  # Fall through to env var lookup inside provider

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
    import os

    model_lower = model.lower()

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
    if anthropic_env_set and not model_lower.startswith(_not_anthropic_prefixes):
        return "anthropic"

    # 2. Prefix-based inference.
    if model_lower.startswith("claude"):
        return "anthropic"
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
    if model_lower.startswith(("llama", "mistral", "qwen", "phi")):
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
        f"     gemini-*, glm-*, kimi-*, llama*, qwen*, mistral*, phi*)."
    )

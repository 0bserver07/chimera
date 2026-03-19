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

from typing import Any

from chimera.providers.base import Provider


def create_provider(
    provider_type: str | None = None,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
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
                "No model specified. Pass model= or set ANTHROPIC_MODEL / OPENAI_MODEL."
            )

    if provider_type is None:
        provider_type = _infer_provider(model)

    factory = get_provider_factory(provider_type)
    if factory is not None:
        return factory(model=model, api_key=api_key, base_url=base_url, **kwargs)

    raise ValueError(
        f"Unknown provider: '{provider_type}'. "
        f"Registered: {list_providers()}"
    )


def _infer_provider(model: str) -> str:
    """Infer provider type from model name or environment."""
    import os

    model_lower = model.lower()
    if model_lower.startswith("claude"):
        return "anthropic"
    if model_lower.startswith(("gpt", "o1", "o3", "codex")):
        return "openai"
    if model_lower.startswith("gemini"):
        return "google"
    if model_lower.startswith(("llama", "mistral", "qwen", "phi")):
        return "ollama"

    # Catalog fallback: check if model is in default catalog
    from chimera.providers.catalog import ProviderCatalog
    catalog = ProviderCatalog.default()
    config = catalog.get(model)
    if config is not None:
        return config.provider_type

    # Fall back to env: if Anthropic credentials are set, assume anthropic-compatible
    if os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"

    raise ValueError(
        f"Cannot infer provider from model name '{model}'. "
        f"Specify provider_type explicitly or set ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN."
    )

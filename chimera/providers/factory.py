# chimera/providers/factory.py
from __future__ import annotations

from chimera.providers.base import Provider


def create_provider(
    provider_type: str | None = None,
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    **kwargs,
) -> Provider:
    """Factory function to create a provider by type or by model name inference.

    Args:
        provider_type: One of "anthropic", "openai", "google", "ollama", "compatible".
                       If None, inferred from model name.
        model: Model identifier (e.g. "claude-sonnet-4-20250514", "gpt-4o", "gemini-2.0-flash").
        api_key: API key for the provider.
        base_url: Base URL override (for compatible/ollama providers).
    """
    # Infer provider from model name if not specified
    if provider_type is None:
        provider_type = _infer_provider(model)

    if provider_type == "anthropic":
        from chimera.providers.anthropic import AnthropicProvider
        return AnthropicProvider(model=model, api_key=api_key)

    elif provider_type == "openai":
        from chimera.providers.openai import OpenAIProvider
        return OpenAIProvider(model=model, api_key=api_key, base_url=base_url)

    elif provider_type == "google":
        from chimera.providers.google import GoogleProvider
        return GoogleProvider(model=model, api_key=api_key)

    elif provider_type == "ollama":
        from chimera.providers.ollama import OllamaProvider
        return OllamaProvider(
            model=model,
            base_url=base_url or "http://localhost:11434",
            **kwargs,
        )

    elif provider_type == "compatible":
        from chimera.providers.compatible import OpenAICompatibleProvider
        if base_url is None:
            raise ValueError("base_url required for 'compatible' provider")
        return OpenAICompatibleProvider(
            model=model,
            base_url=base_url,
            api_key=api_key,
            **kwargs,
        )

    else:
        raise ValueError(
            f"Unknown provider: '{provider_type}'. "
            f"Choose from: anthropic, openai, google, ollama, compatible"
        )


def _infer_provider(model: str) -> str:
    """Infer provider type from model name."""
    model_lower = model.lower()
    if model_lower.startswith("claude"):
        return "anthropic"
    if model_lower.startswith(("gpt", "o1", "o3", "codex")):
        return "openai"
    if model_lower.startswith("gemini"):
        return "google"
    if model_lower.startswith(("llama", "mistral", "qwen", "phi")):
        return "ollama"
    raise ValueError(
        f"Cannot infer provider from model name '{model}'. "
        f"Specify provider_type explicitly."
    )

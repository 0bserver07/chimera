"""Auto-registration for OpenAI Responses API provider."""
from __future__ import annotations

from typing import Any


def register() -> None:
    """Register the OpenAI Responses provider with the factory."""
    try:
        from chimera.providers.registry import register_provider
        from chimera.providers.openai_responses import OpenAIResponsesProvider

        def factory(
            model: str = "",
            api_key: str | None = None,
            base_url: str | None = None,
            **kwargs: Any,
        ) -> OpenAIResponsesProvider:
            return OpenAIResponsesProvider(model=model, api_key=api_key, base_url=base_url)

        register_provider("openai_responses", factory)
    except ImportError:
        pass

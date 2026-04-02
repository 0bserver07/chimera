"""Auto-registration for OpenAI Responses API provider."""


def register() -> None:
    """Register the OpenAI Responses provider with the factory."""
    try:
        from chimera.providers.registry import register_provider
        from chimera.providers.openai_responses import OpenAIResponsesProvider

        def factory(model: str = "", api_key: str | None = None, base_url: str | None = None, **kwargs):  # type: ignore[assignment]
            return OpenAIResponsesProvider(model=model, api_key=api_key, base_url=base_url)

        register_provider("openai_responses", factory)
    except ImportError:
        pass

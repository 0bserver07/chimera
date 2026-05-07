"""Dynamic provider registry mapping model names to configurations."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.providers.base import Provider

from chimera.providers.cost import register_model_cost


@dataclass
class ModelConfig:
    """Configuration for a model in the provider catalog.

    Args:
        model: Model name or prefix (e.g. "deepseek-chat", "bedrock/claude-sonnet-4").
        provider_type: Provider backend ("anthropic", "openai", "google", "ollama",
            "compatible", "modal").
        base_url: API base URL. Use base_url_env to read from environment.
        base_url_env: Environment variable name for base URL.
        api_key_env: Environment variable name for API key.
        context_window: Context window size in tokens.
        supports_tool_use: Whether the model supports tool calling.
        cost: Tuple of (input_cost_per_mtok, output_cost_per_mtok) in USD.
        extra: Additional kwargs passed to the provider constructor.
    """

    model: str
    provider_type: str = "compatible"
    base_url: str | None = None
    base_url_env: str | None = None
    api_key_env: str | None = None
    context_window: int = 128_000
    supports_tool_use: bool = True
    cost: tuple[float, float] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def resolve_base_url(self) -> str | None:
        """Resolve base URL from direct value or environment variable."""
        if self.base_url:
            return self.base_url
        if self.base_url_env:
            return os.environ.get(self.base_url_env)
        return None

    def resolve_api_key(self) -> str | None:
        """Resolve API key from environment variable."""
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


# Built-in catalog entries
_BUILTIN_ENTRIES: list[ModelConfig] = [
    # AWS Bedrock (via OpenAI-compatible gateway)
    ModelConfig("bedrock/claude-sonnet-4", "compatible", base_url_env="AWS_BEDROCK_ENDPOINT",
                api_key_env="AWS_BEDROCK_KEY", context_window=200_000, cost=(3.0, 15.0)),
    ModelConfig("bedrock/claude-haiku-3.5", "compatible", base_url_env="AWS_BEDROCK_ENDPOINT",
                api_key_env="AWS_BEDROCK_KEY", context_window=200_000, cost=(0.80, 4.0)),
    # Azure OpenAI
    ModelConfig("azure/gpt-4o", "compatible", base_url_env="AZURE_OPENAI_ENDPOINT",
                api_key_env="AZURE_OPENAI_KEY", context_window=128_000, cost=(2.50, 10.0)),
    ModelConfig("azure/gpt-4o-mini", "compatible", base_url_env="AZURE_OPENAI_ENDPOINT",
                api_key_env="AZURE_OPENAI_KEY", context_window=128_000, cost=(0.15, 0.60)),
    # Groq
    ModelConfig("groq/llama-3.3-70b", "compatible", base_url="https://api.groq.com/openai/v1",
                api_key_env="GROQ_API_KEY", context_window=128_000, cost=(0.59, 0.79)),
    # DeepSeek
    ModelConfig("deepseek-chat", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=64_000, cost=(0.27, 1.10)),
    ModelConfig("deepseek-reasoner", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=64_000, cost=(0.55, 2.19)),
    # DeepSeek-V4 family. Bare ids hit DeepSeek's hosted OpenAI-compatible
    # API; the ``:cloud``-tagged variant is served via the local Ollama
    # daemon's cloud passthrough (``ollama run deepseek-v4-pro:cloud``).
    # Pricing is a placeholder copying the deepseek-reasoner numbers
    # (input $0.55 / output $2.19 per Mtok); refresh once DeepSeek
    # publishes the V4 list. Context window mirrors DeepSeek-V3's
    # documented 128k window.
    ModelConfig("deepseek-v4", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=128_000, cost=(0.55, 2.19)),
    ModelConfig("deepseek-v4-pro", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=128_000, cost=(0.55, 2.19)),
    ModelConfig("deepseek-v4-pro:cloud", "ollama", base_url_env="OLLAMA_HOST",
                context_window=262_144, cost=(0.55, 2.19)),
]


class ProviderCatalog:
    """Dynamic registry mapping model names to provider configurations.

    Supports slash-namespaced models (e.g. "bedrock/claude-sonnet-4") and
    plain model names. Integrates with create_provider() as a fallback.

    Example:
        ```python
        catalog = ProviderCatalog.default()
        catalog.register(ModelConfig(
            model="my-company/llm",
            provider_type="compatible",
            base_url="https://llm.internal/v1",
        ))
        provider = catalog.create("my-company/llm")
        ```
    """

    def __init__(self) -> None:
        self._entries: dict[str, ModelConfig] = {}
        self._provider_types: dict[str, type] = {}

    @classmethod
    def default(cls) -> ProviderCatalog:
        """Create a catalog pre-loaded with built-in entries."""
        catalog = cls()
        for entry in _BUILTIN_ENTRIES:
            catalog.register(entry)
        return catalog

    def register(self, config: ModelConfig) -> None:
        """Register a model configuration.

        Args:
            config: Model configuration to register.
        """
        self._entries[config.model] = config
        if config.cost is not None:
            register_model_cost(config.model, config.cost[0], config.cost[1])

    def register_provider_type(self, name: str, provider_class: type) -> None:
        """Register a custom provider type.

        Args:
            name: Provider type name (e.g. "vllm").
            provider_class: Provider class to instantiate.
        """
        self._provider_types[name] = provider_class

    def get(self, model: str) -> ModelConfig | None:
        """Look up a model configuration.

        Args:
            model: Model name (e.g. "bedrock/claude-sonnet-4").

        Returns:
            ModelConfig if found, None otherwise.
        """
        return self._entries.get(model)

    def create(self, model: str) -> Provider:
        """Create a provider instance from a catalog entry.

        Args:
            model: Model name registered in the catalog.

        Returns:
            Configured Provider instance.

        Raises:
            KeyError: If model not found in catalog.
        """
        config = self._entries.get(model)
        if config is None:
            raise KeyError(f"Model '{model}' not found in catalog")

        from chimera.providers.factory import create_provider

        base_url = config.resolve_base_url()
        api_key = config.resolve_api_key()

        return create_provider(
            provider_type=config.provider_type,
            model=config.model.split("/")[-1] if "/" in config.model else config.model,
            api_key=api_key,
            base_url=base_url,
            **config.extra,
        )

    @property
    def models(self) -> list[str]:
        """List all registered model names."""
        return list(self._entries.keys())

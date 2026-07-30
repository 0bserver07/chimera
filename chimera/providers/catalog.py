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
    # DeepSeek. Every ``cost=`` below MUST equal the matching prefix in
    # ``chimera.providers.cost.PRICING`` — :meth:`ProviderCatalog.register`
    # feeds these through ``register_model_cost``, so a divergence here silently
    # OVERWRITES the hand table at runtime and bills at this number instead
    # (that is how the stale $0.55/$2.19 V4 rate survived a correction to
    # ``cost.py`` and kept billing). Rates verified
    # 2026-07-25 against https://api-docs.deepseek.com/quick_start/pricing;
    # ``tests/providers/test_catalog_pricing_parity.py`` pins the agreement.
    #
    # ``deepseek-chat`` / ``deepseek-reasoner`` are deprecated aliases for the
    # non-thinking / thinking modes of ``deepseek-v4-flash`` and share its rate.
    ModelConfig("deepseek-chat", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=64_000, cost=(0.14, 0.28)),
    ModelConfig("deepseek-reasoner", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=64_000, cost=(0.14, 0.28)),
    # DeepSeek-V4 family. Bare ids hit DeepSeek's hosted OpenAI-compatible
    # API; the ``:cloud``-tagged variant is served via the local Ollama
    # daemon's cloud passthrough (``ollama run deepseek-v4-pro:cloud``), whose
    # true billing is Ollama's — approximated at the first-party rate. Bare
    # ``deepseek-v4`` is a catch-all pinned to the dearer Pro tier so an
    # unrecognized SKU over-bills rather than under-bills.
    ModelConfig("deepseek-v4-flash", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=128_000, cost=(0.14, 0.28)),
    ModelConfig("deepseek-v4", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=128_000, cost=(0.435, 0.87)),
    ModelConfig("deepseek-v4-pro", "compatible", base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY", context_window=128_000, cost=(0.435, 0.87)),
    ModelConfig("deepseek-v4-pro:cloud", "ollama", base_url_env="OLLAMA_HOST",
                context_window=262_144, cost=(0.435, 0.87)),
    # DeepSeek-V3.1 terminus + V3 coder. Both speak DeepSeek's hosted
    # OpenAI-compatible API, and keep V3.1's own published $0.27 / $1.10 —
    # deliberately NOT re-based onto the V4 rates above.
    # Source: https://api-docs.deepseek.com/quick_start/pricing.
    ModelConfig("deepseek-v3.1-terminus", "compatible",
                base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY",
                context_window=128_000, cost=(0.27, 1.10)),
    ModelConfig("deepseek-coder-v3", "compatible",
                base_url="https://api.deepseek.com/v1",
                api_key_env="DEEPSEEK_API_KEY",
                context_window=128_000, cost=(0.27, 1.10)),
    # Qwen3 family (Alibaba). Bare ids land on the local Ollama daemon
    # (``qwen-`` prefix already routes to ``ollama`` in
    # ``_infer_provider``). Costs are $0/$0 because Ollama-served weights
    # do not surface a price field. For DashScope API access, users set
    # ``$DASHSCOPE_API_KEY`` and pass ``provider_type='compatible'`` plus
    # ``base_url='https://dashscope-intl.aliyuncs.com/compatible-mode/v1'``
    # explicitly. TODO: catalogue ``dashscope/qwen3-*`` SKUs once we have
    # signed-off pricing.
    ModelConfig("qwen3-coder", "ollama", base_url_env="OLLAMA_HOST",
                context_window=131_072, cost=(0.0, 0.0)),
    ModelConfig("qwen3-coder-30b", "ollama", base_url_env="OLLAMA_HOST",
                context_window=131_072, cost=(0.0, 0.0)),
    ModelConfig("qwen3-32b", "ollama", base_url_env="OLLAMA_HOST",
                context_window=131_072, cost=(0.0, 0.0)),
    # GLM family (Zhipu). Anthropic-compatible wire protocol via
    # api.z.ai. Pricing for 4.6 / 5.1 is a placeholder pending Zhipu's
    # public rate sheet; values mirror glm-5 ($2 / $8) for 5.1 and glm-4
    # tier ($0.6 / $2.2) for 4.6. TODO: confirm against
    # https://docs.z.ai/api-reference/llm/chat-completion once published.
    ModelConfig("glm-4.6", "anthropic",
                base_url="https://api.z.ai/api/anthropic",
                api_key_env="ANTHROPIC_AUTH_TOKEN",
                context_window=200_000, cost=(0.6, 2.2)),
    ModelConfig("glm-5.1", "anthropic",
                base_url="https://api.z.ai/api/anthropic",
                api_key_env="ANTHROPIC_AUTH_TOKEN",
                context_window=200_000, cost=(2.0, 8.0)),
    # glm-5.2 — the model this repo actually publishes its flagship scorecard
    # with, and which was MISSING from the catalog until 2026-07-29: a
    # bench-matrix run naming it failed with "Unknown provider: 'glm-5.2'" and
    # recorded an error cell, so the model behind the published numbers could
    # not be re-run through the CLI. Verified served by the same z.ai
    # Anthropic-compat endpoint as 5.1 (bare id and the `[1m]` long-context
    # tag both answer). Rates mirror glm-5.1 pending Zhipu's public sheet —
    # PRICING/PRICING_PLACEHOLDERS in cost.py is the source of truth and keeps
    # glm-5.2 as a declared placeholder, so this row must not diverge from it.
    ModelConfig("glm-5.2", "anthropic",
                base_url="https://api.z.ai/api/anthropic",
                api_key_env="ANTHROPIC_AUTH_TOKEN",
                context_window=200_000, cost=(2.0, 8.0)),
    # GPT-OSS (OpenAI open-weights). Distributed via Ollama; both 20B
    # and 120B run locally on adequately-sized hardware. $0/$0 because
    # local serve is free.
    ModelConfig("gpt-oss-120b", "ollama", base_url_env="OLLAMA_HOST",
                context_window=131_072, cost=(0.0, 0.0)),
    ModelConfig("gpt-oss-20b", "ollama", base_url_env="OLLAMA_HOST",
                context_window=131_072, cost=(0.0, 0.0)),
    # Kimi (Moonshot). Bare ids speak Anthropic-compat at
    # https://api.moonshot.ai/anthropic with $MOONSHOT_API_KEY. Pricing
    # is a placeholder ($0.6 / $2.5) — refresh once Moonshot publishes
    # per-SKU rates for the 0905 preview and k2.5 line. ``:cloud``
    # variants are served by the local Ollama daemon (see kimi
    # docs/mink/models.md), not catalogued here.
    ModelConfig("kimi-k2-0905-preview", "anthropic",
                base_url="https://api.moonshot.ai/anthropic",
                api_key_env="MOONSHOT_API_KEY",
                context_window=200_000, cost=(0.6, 2.5)),
    ModelConfig("kimi-k2.5", "anthropic",
                base_url="https://api.moonshot.ai/anthropic",
                api_key_env="MOONSHOT_API_KEY",
                context_window=200_000, cost=(0.6, 2.5)),
    # Mistral Codestral 2511 (Mistral coder line). Routed through
    # Ollama; the ``mistral`` prefix already maps to the ``ollama``
    # provider in ``_infer_provider``. For Mistral's hosted API, users
    # explicitly pass ``provider_type='compatible'`` +
    # ``base_url='https://api.mistral.ai/v1'`` + ``$MISTRAL_API_KEY``.
    ModelConfig("mistral-codestral-2511", "ollama",
                base_url_env="OLLAMA_HOST",
                context_window=256_000, cost=(0.0, 0.0)),
    # Google Gemma 3 (open weights via Ollama). Local-only; $0/$0.
    ModelConfig("gemma3-27b-instruct", "ollama",
                base_url_env="OLLAMA_HOST",
                context_window=131_072, cost=(0.0, 0.0)),
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

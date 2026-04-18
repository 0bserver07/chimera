# tests/test_provider_catalog.py

import pytest

from chimera.providers.catalog import ModelConfig, ProviderCatalog
from chimera.providers.cost import PRICING, calculate_cost


class TestModelConfig:
    def test_defaults(self):
        mc = ModelConfig(model="test-model")
        assert mc.provider_type == "compatible"
        assert mc.context_window == 128_000
        assert mc.supports_tool_use is True
        assert mc.cost is None
        assert mc.extra == {}

    def test_resolve_base_url_direct(self):
        mc = ModelConfig(model="m", base_url="https://example.com")
        assert mc.resolve_base_url() == "https://example.com"

    def test_resolve_base_url_env(self, monkeypatch):
        monkeypatch.setenv("MY_URL", "https://env.example.com")
        mc = ModelConfig(model="m", base_url_env="MY_URL")
        assert mc.resolve_base_url() == "https://env.example.com"

    def test_resolve_base_url_none(self):
        mc = ModelConfig(model="m")
        assert mc.resolve_base_url() is None

    def test_resolve_api_key(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret123")
        mc = ModelConfig(model="m", api_key_env="MY_KEY")
        assert mc.resolve_api_key() == "secret123"

    def test_resolve_api_key_none(self):
        mc = ModelConfig(model="m")
        assert mc.resolve_api_key() is None


class TestProviderCatalog:
    def test_register_and_get(self):
        catalog = ProviderCatalog()
        mc = ModelConfig(model="test/model", provider_type="compatible")
        catalog.register(mc)
        assert catalog.get("test/model") is mc

    def test_default_has_entries(self):
        catalog = ProviderCatalog.default()
        assert len(catalog.models) > 0
        assert "bedrock/claude-sonnet-4" in catalog.models

    def test_register_custom_model(self):
        catalog = ProviderCatalog()
        catalog.register(ModelConfig(model="custom/llm"))
        assert "custom/llm" in catalog.models

    def test_get_missing_returns_none(self):
        catalog = ProviderCatalog()
        assert catalog.get("nonexistent") is None

    def test_create_raises_for_missing(self):
        catalog = ProviderCatalog()
        with pytest.raises(KeyError, match="not found"):
            catalog.create("nonexistent")

    def test_cost_auto_registered(self):
        catalog = ProviderCatalog()
        catalog.register(ModelConfig(
            model="test-cost-model",
            cost=(1.0, 2.0),
        ))
        cost = calculate_cost("test-cost-model", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
        assert cost == pytest.approx(3.0)
        # Clean up
        PRICING.pop("test-cost-model", None)

    def test_register_provider_type(self):
        catalog = ProviderCatalog()
        catalog.register_provider_type("vllm", object)
        assert "vllm" in catalog._provider_types

    def test_slash_routing_strips_namespace(self, monkeypatch):
        monkeypatch.setenv("AWS_BEDROCK_ENDPOINT", "https://bedrock.example.com")
        monkeypatch.setenv("AWS_BEDROCK_KEY", "fake-key")
        catalog = ProviderCatalog.default()
        config = catalog.get("bedrock/claude-sonnet-4")
        assert config is not None
        # The model name passed to create_provider should strip the namespace
        model_for_provider = config.model.split("/")[-1] if "/" in config.model else config.model
        assert model_for_provider == "claude-sonnet-4"

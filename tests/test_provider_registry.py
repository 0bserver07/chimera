"""Tests for chimera.providers.registry."""
from chimera.providers.registry import (
    register_provider,
    get_provider_factory,
    list_providers,
    unregister_provider,
)
from chimera.providers.base import Provider, Response


class MockProvider(Provider):
    def complete(self, messages, tools=None, temperature=0.0, max_tokens=None):
        return Response(content="mock", tool_calls=[], usage={})
    @property
    def context_window(self):
        return 1000
    @property
    def supports_tool_use(self):
        return True
    @property
    def model_name(self):
        return "mock"


def _mock_factory(model="mock", **kwargs):
    return MockProvider()


def test_register_and_get():
    register_provider("test-provider", _mock_factory)
    factory = get_provider_factory("test-provider")
    assert factory is _mock_factory
    unregister_provider("test-provider")


def test_get_unknown_returns_none():
    assert get_provider_factory("nonexistent-xyz-abc") is None


def test_list_providers():
    register_provider("test-list", _mock_factory)
    assert "test-list" in list_providers()
    unregister_provider("test-list")


def test_unregister():
    register_provider("test-unreg", _mock_factory)
    unregister_provider("test-unreg")
    assert get_provider_factory("test-unreg") is None


def test_builtins_registered_after_ensure():
    from chimera.providers.registry import _ensure_builtins_registered
    _ensure_builtins_registered()
    names = list_providers()
    assert "anthropic" in names
    assert "openai" in names
    assert "google" in names
    assert "ollama" in names
    assert "compatible" in names
    assert "modal" in names

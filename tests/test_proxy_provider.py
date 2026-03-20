"""Tests for ProxyProvider."""
import json
from unittest.mock import patch, MagicMock
from chimera.providers.proxy import ProxyProvider
from chimera.types import Message


def _mock_response(content="hello", tool_calls=None, usage=None):
    resp = MagicMock()
    result = {
        "content": content,
        "tool_calls": tool_calls or [],
        "usage": usage or {"input_tokens": 10, "output_tokens": 5},
    }
    resp.read.return_value = json.dumps(result).encode()
    return resp


def test_complete():
    provider = ProxyProvider(proxy_url="http://localhost:8080", model="test-model")
    with patch("chimera.providers.proxy.urllib.request.urlopen", return_value=_mock_response("world")):
        result = provider.complete([Message.user("hello")])
    assert result.content == "world"
    assert result.usage["input_tokens"] == 10


def test_complete_with_auth():
    provider = ProxyProvider(proxy_url="http://localhost:8080", auth_token="secret", model="m")
    with patch("chimera.providers.proxy.urllib.request.urlopen", return_value=_mock_response()) as mock_open:
        provider.complete([Message.user("hi")])
        req = mock_open.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer secret"


def test_complete_with_tools():
    tc_data = [{"id": "tc1", "name": "bash", "arguments": {"command": "ls"}}]
    provider = ProxyProvider(proxy_url="http://localhost:8080", model="m")
    with patch("chimera.providers.proxy.urllib.request.urlopen",
               return_value=_mock_response(tool_calls=tc_data)):
        result = provider.complete([Message.user("hi")])
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "bash"


def test_properties():
    p = ProxyProvider(proxy_url="http://localhost:8080", model="my-model")
    assert p.model_name == "my-model"
    assert p.context_window == 128000
    assert p.supports_tool_use is True


def test_url_trailing_slash():
    p = ProxyProvider(proxy_url="http://localhost:8080/")
    assert p._proxy_url == "http://localhost:8080"


def test_registry_registration():
    from chimera.providers.registry import get_provider_factory
    factory = get_provider_factory("proxy")
    assert factory is not None


def test_proxy_factory_requires_base_url():
    import pytest
    from chimera.providers.registry import get_provider_factory
    factory = get_provider_factory("proxy")
    with pytest.raises(ValueError, match="base_url required"):
        factory(model="m")

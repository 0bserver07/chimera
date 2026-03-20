"""Proxy provider — routes LLM calls through an HTTP relay."""
from __future__ import annotations

import json
import urllib.request
from typing import Any
from collections.abc import Iterator

from chimera.providers.base import Provider, Response, StreamEvent
from chimera.types import Message, ToolCall


class ProxyProvider(Provider):
    """Routes LLM calls through an HTTP proxy endpoint.

    Useful for centralized key management, cost tracking, or running
    agents in environments without direct API access.

    The proxy must expose:
        POST /api/complete — non-streaming completion
        POST /api/stream — streaming completion (SSE, optional)

    Args:
        proxy_url: Base URL of the proxy (e.g. "http://localhost:8080").
        auth_token: Optional Bearer token for proxy authentication.
        model: Model identifier forwarded to the proxy.
    """

    def __init__(self, proxy_url: str, auth_token: str | None = None,
                 model: str = "") -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._auth_token = auth_token
        self._model = model

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload).encode()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        req = urllib.request.Request(
            f"{self._proxy_url}/api/complete",
            data=data,
            headers=headers,
            method="POST",
        )
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read())

        tool_calls = []
        for tc in result.get("tool_calls", []):
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=tc.get("name", ""),
                arguments=tc.get("arguments", {}),
            ))

        return Response(
            content=result.get("content", ""),
            tool_calls=tool_calls,
            usage=result.get("usage", {"input_tokens": 0, "output_tokens": 0}),
        )

    @property
    def context_window(self) -> int:
        return 128000  # Default; proxy should report actual

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model


# Self-register with provider registry
from chimera.providers.registry import register_provider as _register  # noqa: E402


def _proxy_factory(model: str = "", base_url: str | None = None,
                   api_key: str | None = None, **kwargs: Any) -> ProxyProvider:
    if not base_url:
        raise ValueError("base_url required for 'proxy' provider")
    return ProxyProvider(proxy_url=base_url, auth_token=api_key, model=model)


_register("proxy", _proxy_factory)

# chimera/providers/compatible.py
from __future__ import annotations

import json
import os
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class OpenAICompatibleProvider(Provider):
    """Generic OpenAI-compatible provider.

    Works with: OpenRouter, Together, Fireworks, Groq, vLLM, LiteLLM,
    Anthropic Coding API (via OpenAI compatibility), any /v1/chat/completions endpoint.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        context_length: int = 128_000,
    ) -> None:
        if httpx is None:
            raise ImportError("pip install httpx")
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            **(headers or {}),
        }
        self._context_length = context_length

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> Response:
        api_messages = self._convert_messages(messages)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = self._convert_tools(tools)

        endpoint = f"{self._base_url}/chat/completions"
        resp = httpx.post(endpoint, json=payload, headers=self._headers, timeout=300)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        content = choice["message"].get("content") or ""

        tool_calls = []
        for tc in choice["message"].get("tool_calls", []) or []:
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            tool_calls.append(ToolCall(
                id=tc.get("id", f"call_{id(tc)}"),
                name=tc["function"]["name"],
                arguments=args,
            ))

        usage = data.get("usage", {})
        return Response(
            content=content,
            tool_calls=tool_calls,
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        api_messages = []
        for msg in messages:
            if msg.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.call_id,
                    "content": msg.content,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                tc_list = []
                for tc in msg.tool_calls:
                    tc_list.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    })
                api_messages.append({
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": tc_list,
                })
            else:
                api_messages.append({"role": msg.role, "content": msg.content})
        return api_messages

    def _convert_tools(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", tool.get("parameters", {})),
                },
            })
        return result

    @property
    def context_window(self) -> int:
        return self._context_length

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model


from chimera.providers.registry import register_provider as _register  # noqa: E402
_register("compatible", lambda model="", base_url=None, api_key=None, **kw: OpenAICompatibleProvider(model=model, base_url=base_url or "", api_key=api_key, **kw))

# chimera/providers/modal.py
from __future__ import annotations

import json
import os
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import modal  # type: ignore[import-not-found]
except ImportError:
    modal = None  # type: ignore[assignment]

try:
    import httpx  # type: ignore[import-not-found]
except ImportError:
    httpx = None  # type: ignore[assignment]


class ModalProvider(Provider):
    """Modal serverless GPU inference provider.

    Deploys a vLLM container on Modal, then calls its OpenAI-compatible
    /v1/chat/completions endpoint via httpx.

    Requires: pip install modal httpx
    """

    def __init__(
        self,
        model: str,
        gpu: str = "H100",
        token_id: str | None = None,
        token_secret: str | None = None,
        base_url: str | None = None,
        context_length: int = 131_072,
    ) -> None:
        if modal is None:
            raise ImportError("pip install modal")
        if httpx is None:
            raise ImportError("pip install httpx")

        self._model = model
        self._gpu = gpu
        self._token_id = token_id or os.environ.get("MODAL_TOKEN_ID", "")
        self._token_secret = token_secret or os.environ.get("MODAL_TOKEN_SECRET", "")
        self._base_url = base_url
        self._context_length = context_length

    def _get_base_url(self) -> str:
        if self._base_url:
            return self._base_url.rstrip("/")
        raise ValueError(
            "No base_url configured. Deploy a vLLM container on Modal first, "
            "then pass the endpoint URL as base_url."
        )

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
        cancel_event: Any = None,  # accepted for Liskov; not yet plumbed
    ) -> Response:
        api_messages = self._convert_messages(messages)
        base_url = self._get_base_url()

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = self._convert_tools(tools)

        endpoint = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
        }

        assert httpx is not None  # checked in __init__
        resp = httpx.post(endpoint, json=payload, headers=headers, timeout=300)
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
        api_messages: list[dict[str, Any]] = []
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
_register("modal", lambda model="", base_url=None, **kw: ModalProvider(model=model, base_url=base_url, **kw))

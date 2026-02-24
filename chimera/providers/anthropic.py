from __future__ import annotations

import os
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]


class AnthropicProvider(Provider):
    """Anthropic Claude provider."""

    CONTEXT_WINDOWS = {
        "claude-opus-4": 200_000,
        "claude-sonnet-4": 200_000,
        "claude-haiku-3.5": 200_000,
    }

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None) -> None:
        if anthropic is None:
            raise ImportError("pip install chimera-ai[anthropic]")
        self._model = model
        client_kwargs: dict[str, Any] = {
            "api_key": api_key or os.environ.get("ANTHROPIC_API_KEY"),
        }
        if base_url or os.environ.get("ANTHROPIC_BASE_URL"):
            client_kwargs["base_url"] = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        self._client = anthropic.Anthropic(**client_kwargs)

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        # Separate system message
        system_msg = None
        api_messages = []
        for msg in messages:
            if msg.role == "system":
                system_msg = msg.content
            elif msg.role == "tool":
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.call_id,
                        "content": msg.content,
                    }],
                })
            elif msg.role == "assistant" and msg.tool_calls:
                content: list[dict[str, Any]] = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                api_messages.append({"role": "assistant", "content": content})
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            kwargs["tools"] = tools

        response = self._client.messages.create(**kwargs)

        # Parse response
        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))

        return Response(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )

    @property
    def context_window(self) -> int:
        for prefix, size in self.CONTEXT_WINDOWS.items():
            if self._model.startswith(prefix):
                return size
        return 200_000  # Default

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model

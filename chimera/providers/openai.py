# chimera/providers/openai.py
from __future__ import annotations

import json
import os
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]


class OpenAIProvider(Provider):
    """OpenAI Chat Completions provider (GPT-4o, o1, o3, Codex, etc.)."""

    CONTEXT_WINDOWS = {
        "gpt-4o": 128_000,
        "gpt-4-turbo": 128_000,
        "gpt-4": 8_192,
        "gpt-3.5-turbo": 16_385,
        "o1": 200_000,
        "o3": 200_000,
        "codex": 200_000,
    }

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if openai is None:
            raise ImportError("pip install chimera-ai[openai]")
        self._model = model
        self._client = openai.OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url,
        )

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        api_messages = self._convert_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = self._convert_tools(tools)

        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # Extract text
        content = choice.message.content or ""

        # Extract tool calls
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                ))

        return Response(
            content=content,
            tool_calls=tool_calls,
            usage={
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            },
        )

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert Chimera messages to OpenAI format."""
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
        """Convert Anthropic tool schema to OpenAI function schema."""
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
        for prefix, size in self.CONTEXT_WINDOWS.items():
            if self._model.startswith(prefix):
                return size
        return 128_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model

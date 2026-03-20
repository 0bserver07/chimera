# chimera/providers/openai.py
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

from chimera.providers.base import Provider, Response, StreamEvent, ToolSchema
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

    # ------------------------------------------------------------------
    # Request / response helpers
    # ------------------------------------------------------------------

    def _prepare_request(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Build the kwargs dict for the OpenAI chat completions API."""
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
        return kwargs

    @staticmethod
    def _parse_response(response: Any) -> Response:
        """Convert an OpenAI API response into a :class:`Response`."""
        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls: list[ToolCall] = []
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> Response:
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens)
        response = self._client.chat.completions.create(**kwargs)
        return self._parse_response(response)

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> Iterator[StreamEvent]:
        """Stream a response using the OpenAI chat completions stream API."""
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        # Accumulate tool calls by index (OpenAI sends deltas with index)
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        response_stream = self._client.chat.completions.create(**kwargs)
        for chunk in response_stream:
            if not chunk.choices:
                # Final chunk may have usage only
                if chunk.usage:
                    usage = {
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                    }
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Text content
            if delta and delta.content:
                yield StreamEvent(type="text_delta", content=delta.content)

            # Tool call deltas
            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        # New tool call
                        tool_calls_acc[idx] = {
                            "id": tc_delta.id or "",
                            "name": tc_delta.function.name if tc_delta.function and tc_delta.function.name else "",
                            "arguments": "",
                        }
                        yield StreamEvent(
                            type="tool_call_start",
                            tool_call=ToolCall(
                                id=tool_calls_acc[idx]["id"],
                                name=tool_calls_acc[idx]["name"],
                                arguments={},
                            ),
                        )
                    # Accumulate argument fragments
                    if tc_delta.function and tc_delta.function.arguments:
                        tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments
                        yield StreamEvent(
                            type="tool_call_delta",
                            content=tc_delta.function.arguments,
                        )

            # Finish reason signals completion
            if choice.finish_reason:
                # Emit tool_call_complete for all accumulated tool calls
                for idx in sorted(tool_calls_acc):
                    acc = tool_calls_acc[idx]
                    try:
                        args = json.loads(acc["arguments"]) if acc["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    yield StreamEvent(
                        type="tool_call_complete",
                        tool_call=ToolCall(id=acc["id"], name=acc["name"], arguments=args),
                    )
                tool_calls_acc.clear()

        yield StreamEvent(type="done", usage=usage)

    # ------------------------------------------------------------------
    # Async API (native, using AsyncOpenAI)
    # ------------------------------------------------------------------

    @property
    def _aclient(self) -> Any:
        """Lazy-initialized async OpenAI client."""
        if not hasattr(self, "_async_client"):
            client_kwargs: dict[str, Any] = {
                "api_key": self._client.api_key,
            }
            if self._client.base_url and str(self._client.base_url) != "https://api.openai.com/v1":
                client_kwargs["base_url"] = str(self._client.base_url)
            self._async_client = openai.AsyncOpenAI(**client_kwargs)  # type: ignore[union-attr]
        return self._async_client

    async def async_complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> Response:
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens)
        response = await self._aclient.chat.completions.create(**kwargs)
        return self._parse_response(response)

    async def async_stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> AsyncIterator[StreamEvent]:
        """Async stream using the OpenAI async chat completions API."""
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        tool_calls_acc: dict[int, dict[str, Any]] = {}
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        response_stream = await self._aclient.chat.completions.create(**kwargs)
        async for chunk in response_stream:
            if not chunk.choices:
                if chunk.usage:
                    usage = {
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                    }
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if delta and delta.content:
                yield StreamEvent(type="text_delta", content=delta.content)

            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc_delta.id or "",
                            "name": tc_delta.function.name if tc_delta.function and tc_delta.function.name else "",
                            "arguments": "",
                        }
                        yield StreamEvent(
                            type="tool_call_start",
                            tool_call=ToolCall(
                                id=tool_calls_acc[idx]["id"],
                                name=tool_calls_acc[idx]["name"],
                                arguments={},
                            ),
                        )
                    if tc_delta.function and tc_delta.function.arguments:
                        tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments
                        yield StreamEvent(
                            type="tool_call_delta",
                            content=tc_delta.function.arguments,
                        )

            if choice.finish_reason:
                for idx in sorted(tool_calls_acc):
                    acc = tool_calls_acc[idx]
                    try:
                        args = json.loads(acc["arguments"]) if acc["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    yield StreamEvent(
                        type="tool_call_complete",
                        tool_call=ToolCall(id=acc["id"], name=acc["name"], arguments=args),
                    )
                tool_calls_acc.clear()

        yield StreamEvent(type="done", usage=usage)

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


from chimera.providers.registry import register_provider as _register  # noqa: E402
_register("openai", lambda model="", api_key=None, base_url=None, **kw: OpenAIProvider(model=model, api_key=api_key, base_url=base_url))

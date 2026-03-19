from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

from chimera.providers.base import Provider, Response, StreamEvent, ToolSchema
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

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        enable_cache: bool = False,
        enable_thinking: bool = False,
        thinking_budget: int = 10_000,
    ) -> None:
        if anthropic is None:
            raise ImportError("pip install chimera-ai[anthropic]")
        self._model = model
        self._enable_cache = enable_cache
        self._enable_thinking = enable_thinking
        self._thinking_budget = thinking_budget
        client_kwargs: dict[str, Any] = {
            "api_key": api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_AUTH_TOKEN"),
        }
        if base_url or os.environ.get("ANTHROPIC_BASE_URL"):
            client_kwargs["base_url"] = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        self._client = anthropic.Anthropic(**client_kwargs)

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
        """Build the kwargs dict for the Anthropic messages API."""
        system_msg = None
        api_messages: list[dict[str, Any]] = []
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
        }

        # Extended thinking — requires temperature=1 and uses budget_tokens
        if self._enable_thinking:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }
            kwargs["temperature"] = 1  # Required for extended thinking
        else:
            kwargs["temperature"] = temperature

        # System message — with optional prompt caching
        if system_msg:
            if self._enable_cache:
                kwargs["system"] = [
                    {"type": "text", "text": system_msg, "cache_control": {"type": "ephemeral"}},
                ]
            else:
                kwargs["system"] = system_msg

        # Tools — with optional prompt caching on last tool definition
        if tools:
            if self._enable_cache and tools:
                cached_tools = [*tools]
                cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
                kwargs["tools"] = cached_tools
            else:
                kwargs["tools"] = tools

        return kwargs

    @staticmethod
    def _parse_response(response: Any) -> Response:
        """Convert an Anthropic API response into a :class:`Response`."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        thinking_text = ""
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))
            elif block.type == "thinking":
                thinking_text = getattr(block, "thinking", "")

        usage: dict[str, int] = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        # Cache tokens (prompt caching)
        cache_creation = getattr(response.usage, "cache_creation_input_tokens", None)
        cache_read = getattr(response.usage, "cache_read_input_tokens", None)
        if cache_creation is not None:
            usage["cache_creation_input_tokens"] = cache_creation
        if cache_read is not None:
            usage["cache_read_input_tokens"] = cache_read

        resp = Response(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
        )
        if thinking_text:
            resp.usage["thinking_tokens"] = len(thinking_text.split())  # approximate
        return resp

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens)
        response = self._client.messages.create(**kwargs)
        return self._parse_response(response)

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream a response using the Anthropic messages stream API."""
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens)

        # Track tool call state across events
        current_tool_id: str | None = None
        current_tool_name: str | None = None
        current_tool_json = ""

        with self._client.messages.stream(**kwargs) as stream:
            for event in stream:
                yield from self._map_anthropic_event(
                    event,
                    current_tool_id,
                    current_tool_name,
                    current_tool_json,
                )
                # Update tracking state
                current_tool_id, current_tool_name, current_tool_json = (
                    self._update_tool_state(
                        event, current_tool_id, current_tool_name, current_tool_json,
                    )
                )

            # Emit final tool_call_complete if stream ends mid-tool
            if current_tool_id is not None:
                try:
                    args = json.loads(current_tool_json) if current_tool_json else {}
                except json.JSONDecodeError:
                    args = {}
                yield StreamEvent(
                    type="tool_call_complete",
                    tool_call=ToolCall(
                        id=current_tool_id,
                        name=current_tool_name or "",
                        arguments=args,
                    ),
                )

            # Done event with usage
            final = stream.get_final_message()
            yield StreamEvent(
                type="done",
                usage={
                    "input_tokens": final.usage.input_tokens,
                    "output_tokens": final.usage.output_tokens,
                },
            )

    @staticmethod
    def _map_anthropic_event(
        event: Any,
        current_tool_id: str | None,
        current_tool_name: str | None,
        current_tool_json: str,
    ) -> Iterator[StreamEvent]:
        """Map a single Anthropic SDK event to zero or more StreamEvents."""
        event_type = getattr(event, "type", None)

        if event_type == "content_block_start":
            block = event.content_block
            if block.type == "tool_use":
                yield StreamEvent(
                    type="tool_call_start",
                    tool_call=ToolCall(id=block.id, name=block.name, arguments={}),
                )

        elif event_type == "content_block_delta":
            delta = event.delta
            if delta.type == "text_delta":
                yield StreamEvent(type="text_delta", content=delta.text)
            elif delta.type == "input_json_delta":
                yield StreamEvent(type="tool_call_delta", content=delta.partial_json)

        elif event_type == "content_block_stop":
            # If we were accumulating a tool call, it's now complete
            if current_tool_id is not None:
                try:
                    args = json.loads(current_tool_json) if current_tool_json else {}
                except json.JSONDecodeError:
                    args = {}
                yield StreamEvent(
                    type="tool_call_complete",
                    tool_call=ToolCall(
                        id=current_tool_id,
                        name=current_tool_name or "",
                        arguments=args,
                    ),
                )

    @staticmethod
    def _update_tool_state(
        event: Any,
        current_tool_id: str | None,
        current_tool_name: str | None,
        current_tool_json: str,
    ) -> tuple[str | None, str | None, str]:
        """Return updated tool-tracking state after processing *event*."""
        event_type = getattr(event, "type", None)

        if event_type == "content_block_start":
            block = event.content_block
            if block.type == "tool_use":
                return block.id, block.name, ""
        elif event_type == "content_block_delta":
            delta = event.delta
            if delta.type == "input_json_delta":
                return current_tool_id, current_tool_name, current_tool_json + delta.partial_json
        elif event_type == "content_block_stop":
            if current_tool_id is not None:
                return None, None, ""

        return current_tool_id, current_tool_name, current_tool_json

    # ------------------------------------------------------------------
    # Async API (native, using AsyncAnthropic)
    # ------------------------------------------------------------------

    @property
    def _aclient(self) -> Any:
        """Lazy-initialized async Anthropic client."""
        if not hasattr(self, "_async_client"):
            client_kwargs: dict[str, Any] = {
                "api_key": self._client.api_key,
            }
            if self._client.base_url and str(self._client.base_url) != "https://api.anthropic.com":
                client_kwargs["base_url"] = str(self._client.base_url)
            self._async_client = anthropic.AsyncAnthropic(**client_kwargs)  # type: ignore[union-attr]
        return self._async_client

    async def async_complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Response:
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens)
        response = await self._aclient.messages.create(**kwargs)
        return self._parse_response(response)

    async def async_stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Async stream using the Anthropic async messages stream API."""
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens)

        current_tool_id: str | None = None
        current_tool_name: str | None = None
        current_tool_json = ""

        async with self._aclient.messages.stream(**kwargs) as stream:
            async for event in stream:
                for se in self._map_anthropic_event(
                    event, current_tool_id, current_tool_name, current_tool_json,
                ):
                    yield se
                current_tool_id, current_tool_name, current_tool_json = (
                    self._update_tool_state(
                        event, current_tool_id, current_tool_name, current_tool_json,
                    )
                )

            if current_tool_id is not None:
                try:
                    args = json.loads(current_tool_json) if current_tool_json else {}
                except json.JSONDecodeError:
                    args = {}
                yield StreamEvent(
                    type="tool_call_complete",
                    tool_call=ToolCall(
                        id=current_tool_id,
                        name=current_tool_name or "",
                        arguments=args,
                    ),
                )

            final = await stream.get_final_message()
            yield StreamEvent(
                type="done",
                usage={
                    "input_tokens": final.usage.input_tokens,
                    "output_tokens": final.usage.output_tokens,
                },
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

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


from chimera.providers.registry import register_provider as _register  # noqa: E402
_register("anthropic", lambda model="", api_key=None, base_url=None, **kw: AnthropicProvider(model=model, api_key=api_key, base_url=base_url))

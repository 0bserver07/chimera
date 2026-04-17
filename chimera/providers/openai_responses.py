"""OpenAI Responses API provider.

Uses the newer ``/v1/responses`` endpoint instead of ``/v1/chat/completions``.
This is the API format used by Codex and newer OpenAI integrations.

Falls back to chat completions if the responses endpoint is unavailable.
"""
from __future__ import annotations

import json
import os
from typing import Any

from chimera.providers.base import Provider, Response, StreamEvent, ToolSchema
from chimera.types import Message, ToolCall

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class OpenAIResponsesProvider(Provider):
    """OpenAI Responses API provider.

    Uses the newer /v1/responses endpoint instead of /v1/chat/completions.
    This is the API format used by Codex and newer OpenAI integrations.

    Falls back to chat completions if responses endpoint is unavailable.
    """

    ENDPOINT = "/v1/responses"
    FALLBACK_ENDPOINT = "/v1/chat/completions"

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
        if httpx is None:
            raise ImportError("pip install httpx")
        self._model = model
        self._base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")).rstrip("/")
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=120,
        )
        self._use_responses_api = True  # Try responses API first

    # ------------------------------------------------------------------
    # Provider ABC
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def context_window(self) -> int:
        for prefix, size in self.CONTEXT_WINDOWS.items():
            if self._model.startswith(prefix):
                return size
        return 128_000

    @property
    def supports_tool_use(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Complete
    # ------------------------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> Response:
        """Send completion request."""
        if self._use_responses_api:
            try:
                return self._complete_responses(messages, tools, **kwargs)
            except Exception:
                self._use_responses_api = False
        return self._complete_chat(messages, tools, **kwargs)

    def _complete_responses(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> Response:
        """Use /v1/responses endpoint."""
        payload: dict[str, Any] = {
            "model": self._model,
            "input": self._format_messages_responses(messages),
        }
        if tools:
            payload["tools"] = self._format_tools_responses(tools)

        resp = self._client.post(self.ENDPOINT, json=payload)
        resp.raise_for_status()
        data = resp.json()

        return self._parse_responses_result(data)

    def _complete_chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> Response:
        """Fallback to /v1/chat/completions."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": self._format_messages_chat(messages),
        }
        if tools:
            payload["tools"] = self._format_tools_chat(tools)

        resp = self._client.post(self.FALLBACK_ENDPOINT, json=payload)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        msg = choice["message"]
        tool_calls: list[ToolCall] = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_calls.append(ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=(
                        json.loads(tc["function"]["arguments"])
                        if isinstance(tc["function"]["arguments"], str)
                        else tc["function"]["arguments"]
                    ),
                ))

        return Response(
            content=msg.get("content", ""),
            tool_calls=tool_calls,
            usage=self._normalise_usage(data.get("usage", {})),
        )

    @staticmethod
    def _normalise_usage(raw: dict[str, Any]) -> dict[str, int]:
        """Translate OpenAI usage keys into Chimera's (input_tokens/output_tokens).

        Responses API uses ``input_tokens``/``output_tokens`` directly while
        chat completions uses ``prompt_tokens``/``completion_tokens``.
        Reasoning and cached tokens are preserved when present.
        """
        if not raw:
            return {}
        usage: dict[str, int] = {
            "input_tokens": int(
                raw.get("input_tokens", raw.get("prompt_tokens", 0)) or 0,
            ),
            "output_tokens": int(
                raw.get("output_tokens", raw.get("completion_tokens", 0)) or 0,
            ),
        }
        # Responses API nests reasoning under output_tokens_details
        details = raw.get("output_tokens_details") or raw.get(
            "completion_tokens_details",
        )
        if isinstance(details, dict):
            reasoning = details.get("reasoning_tokens")
            if reasoning:
                usage["reasoning_tokens"] = int(reasoning)
        prompt_details = raw.get("input_tokens_details") or raw.get(
            "prompt_tokens_details",
        )
        if isinstance(prompt_details, dict):
            cached = prompt_details.get("cached_tokens")
            if cached:
                usage["cache_read_input_tokens"] = int(cached)
        return usage

    # ------------------------------------------------------------------
    # Message formatting
    # ------------------------------------------------------------------

    def _format_messages_responses(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Format messages for the Responses API."""
        result: list[dict[str, Any]] = []
        for msg in messages:
            role = getattr(msg, "role", "user")
            content = getattr(msg, "content", str(msg))
            if role == "system":
                result.append({"role": "system", "content": content})
            elif role == "user":
                result.append({"role": "user", "content": content})
            elif role == "assistant":
                result.append({"role": "assistant", "content": content})
            elif role == "tool":
                call_id = getattr(msg, "call_id", "")
                result.append({"type": "function_call_output", "call_id": call_id, "output": content})
        return result

    def _format_messages_chat(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Format messages for the Chat Completions API."""
        result: list[dict[str, Any]] = []
        for msg in messages:
            role = getattr(msg, "role", "user")
            content = getattr(msg, "content", str(msg))
            result.append({"role": role, "content": content})
        return result

    # ------------------------------------------------------------------
    # Tool formatting
    # ------------------------------------------------------------------

    def _format_tools_responses(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        """Format tools for the Responses API."""
        return [
            {
                "type": "function",
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {}),
            }
            for t in tools
        ]

    def _format_tools_chat(self, tools: list[ToolSchema]) -> list[dict[str, Any]]:
        """Format tools for the Chat Completions API."""
        return [{"type": "function", "function": t} for t in tools]

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_responses_result(self, data: dict[str, Any]) -> Response:
        """Parse a Responses API result into a :class:`Response`."""
        content = ""
        tool_calls: list[ToolCall] = []

        for item in data.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        content += part.get("text", "")
            elif item.get("type") == "function_call":
                raw_args = item.get("arguments", "{}")
                tool_calls.append(ToolCall(
                    id=item.get("call_id", ""),
                    name=item.get("name", ""),
                    arguments=(
                        json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    ),
                ))

        return Response(
            content=content,
            tool_calls=tool_calls,
            usage=self._normalise_usage(data.get("usage", {})),
        )

    # ------------------------------------------------------------------
    # Async
    # ------------------------------------------------------------------

    async def async_complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> Response:
        """Async version -- delegates to sync via a thread."""
        import asyncio
        return await asyncio.to_thread(self.complete, messages, tools, **kwargs)

# chimera/providers/compatible.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.types import Message, ToolCall

try:
    import httpx  # type: ignore[import-not-found]
except ImportError:
    httpx = None  # type: ignore[assignment]


@dataclass(frozen=True)
class CompatFlags:
    """Quirk parameterization for OpenAI-compatible backends.

    One wire protocol serves many backends, but they disagree on small
    request/response details. Rather than fork the provider per backend,
    the differences live in this flags table — auto-detected from the model
    id by :func:`detect_compat_flags`, overridable via the provider ctor.

    Attributes:
        max_tokens_field: Request field naming the output cap. Newer OpenAI
            reasoning models require ``max_completion_tokens``; most compat
            backends only accept ``max_tokens``.
        supports_temperature: Some reasoning models reject ``temperature``
            outright; when ``False`` it is omitted from the payload.
        extra_payload: Backend-specific request params merged into every
            payload (e.g. a reasoning-effort knob).
    """

    max_tokens_field: str = "max_tokens"
    supports_temperature: bool = True
    extra_payload: dict[str, Any] = field(default_factory=dict)


#: Model-id prefixes that require the ``max_completion_tokens`` field and
#: reject ``temperature`` (OpenAI reasoning-model conventions).
_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def detect_compat_flags(model: str) -> CompatFlags:
    """Best-effort :class:`CompatFlags` for *model* by id convention.

    Args:
        model: Upstream model id (provider prefixes like ``openai/`` are
            tolerated).

    Returns:
        Flags matching known conventions; the permissive default otherwise.
    """
    bare = model.lower().split("/")[-1]
    if bare.startswith(_REASONING_PREFIXES):
        return CompatFlags(max_tokens_field="max_completion_tokens", supports_temperature=False)
    return CompatFlags()


class OpenAICompatibleProvider(Provider):
    """Generic OpenAI-compatible provider.

    Works with: OpenRouter, Together, Fireworks, Groq, vLLM, LiteLLM,
    Anthropic Coding API (via OpenAI compatibility), any /v1/chat/completions endpoint.

    Backend quirks are parameterized by :class:`CompatFlags` (auto-detected
    from the model id, overridable via ``flags=``) instead of per-backend
    subclasses. On a 400 that names the max-tokens field, the request is
    retried once with the alternate field and the corrected flags stick for
    the session.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        context_length: int = 128_000,
        extra_headers: dict[str, str] | None = None,
        flags: CompatFlags | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            model: Upstream model id (e.g. ``"anthropic/claude-sonnet-4"``).
            base_url: API root (e.g. ``"https://openrouter.ai/api/v1"``).
            api_key: Bearer token. Falls back to ``$OPENAI_API_KEY``.
            headers: Optional override map merged on top of the default
                ``Content-Type`` + ``Authorization`` pair. Kept for
                backwards compatibility with existing callers.
            context_length: Advertised context window (informational).
            extra_headers: Additional headers attached to every request.
                Distinct from *headers* purely as a naming hint for
                cosmetic-but-recommended fields (OpenRouter's
                ``HTTP-Referer`` / ``X-Title``, e.g.). Merged after
                *headers* so an explicit *extra_headers* entry wins on
                key collision.
            flags: Backend quirk parameterization. ``None`` auto-detects
                from the model id via :func:`detect_compat_flags`.
        """
        if httpx is None:
            raise ImportError("pip install httpx")
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            **(headers or {}),
            **(extra_headers or {}),
        }
        self._context_length = context_length
        self._flags = flags if flags is not None else detect_compat_flags(model)

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

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
        }
        if self._flags.supports_temperature:
            payload["temperature"] = temperature
        if max_tokens:
            payload[self._flags.max_tokens_field] = max_tokens
        if self._flags.extra_payload:
            payload.update(self._flags.extra_payload)
        if tools:
            payload["tools"] = self._convert_tools(tools)

        endpoint = f"{self._base_url}/chat/completions"
        assert httpx is not None  # checked in __init__
        resp = httpx.post(endpoint, json=payload, headers=self._headers, timeout=300)
        if resp.status_code == 400 and max_tokens:
            # Self-correct a wrong max-tokens field name once: backends that
            # want the other field say so in the error body. The corrected
            # flags stick for the rest of the session.
            body = resp.text or ""
            other = (
                "max_completion_tokens"
                if self._flags.max_tokens_field == "max_tokens"
                else "max_tokens"
            )
            if other in body or self._flags.max_tokens_field in body:
                payload.pop(self._flags.max_tokens_field, None)
                payload[other] = max_tokens
                self._flags = CompatFlags(
                    max_tokens_field=other,
                    supports_temperature=self._flags.supports_temperature,
                    extra_payload=self._flags.extra_payload,
                )
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
        usage_out: dict[str, int] = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        }
        # Cache-hit visibility where the backend reports it (OpenAI-style
        # prompt_tokens_details.cached_tokens) — additive key, absent otherwise.
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens")
        if isinstance(cached, int) and cached > 0:
            usage_out["cache_read_tokens"] = cached
        return Response(
            content=content,
            tool_calls=tool_calls,
            usage=usage_out,
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
_register("compatible", lambda model="", base_url=None, api_key=None, **kw: OpenAICompatibleProvider(model=model, base_url=base_url or "", api_key=api_key, **kw))

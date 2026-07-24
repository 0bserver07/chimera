# chimera/providers/compatible.py
from __future__ import annotations

import json
import os
from typing import Any

from chimera.providers.base import Provider, Response, ToolSchema
from chimera.providers.capabilities import (
    CompatFlags,
    ProviderCapabilities,
    WireProtocol,
    resolve_capabilities,
)
from chimera.types import Message, ToolCall

try:
    import httpx  # type: ignore[import-not-found]
except ImportError:
    httpx = None  # type: ignore[assignment]

# ``CompatFlags`` now lives in ``chimera.providers.capabilities`` as the
# OpenAI-compat request projection of the unified capability matrix; it is
# re-exported here so existing callers (``from chimera.providers.compatible
# import CompatFlags``) keep working unchanged.
__all__ = [
    "CompatFlags",
    "OpenAICompatibleProvider",
    "detect_compat_flags",
]


def detect_compat_flags(model: str) -> CompatFlags:
    """Best-effort :class:`CompatFlags` for *model* by id convention.

    Thin façade over the capability matrix: resolves the OpenAI-compat
    capabilities for *model* (a leading ``provider/`` namespace is tolerated
    and stripped) and projects them onto the three request knobs. The
    reasoning-model conventions — ``o1``/``o3``/``o4``/``gpt-5`` want
    ``max_completion_tokens`` and reject ``temperature`` — now live in the
    matrix as data rather than a local prefix tuple.

    Args:
        model: Upstream model id (provider prefixes like ``openai/`` are
            tolerated).

    Returns:
        Flags matching known conventions; the permissive default otherwise.
    """
    bare = model.split("/")[-1]
    return resolve_capabilities(WireProtocol.OPENAI_COMPAT, model=bare).to_compat_flags()


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
        provider: str | None = None,
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
            flags: Backend quirk parameterization. ``None`` derives them from
                the capability matrix (see *provider*), projecting the
                resolved :class:`~chimera.providers.capabilities.ProviderCapabilities`
                onto the OpenAI-compat request knobs.
            provider: Optional provider name used to resolve provider-level
                capability overrides from the matrix (e.g. ``"acmecloud"``). When
                ``None``, only the ``openai-compat`` protocol default plus any
                model-prefix override apply — the historical behaviour.
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
        self._provider = provider
        # Source quirks from the unified capability matrix (protocol default +
        # optional provider override + model-prefix override), then project to
        # the request-shaping flags. An explicit ``flags=`` still wins.
        self._capabilities: ProviderCapabilities = resolve_capabilities(
            WireProtocol.OPENAI_COMPAT, provider=provider, model=model.split("/")[-1],
        )
        self._flags = flags if flags is not None else self._capabilities.to_compat_flags()

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
        # ``strict: true`` asks the backend to constrain generated arguments to
        # the JSON schema. Gated on the matrix so it stays absent for backends
        # that don't advertise strict-tool support (the historical shape).
        strict = self._capabilities.supports_strict_tools
        result = []
        for tool in tools:
            function: dict[str, Any] = {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", tool.get("parameters", {})),
            }
            if strict:
                function["strict"] = True
            result.append({"type": "function", "function": function})
        return result

    @property
    def request_headers(self) -> dict[str, str]:
        """Per-request HTTP headers (a copy) sent with every API call.

        This is the documented header-injection surface for the
        ``provider_request`` interception seam
        (:mod:`chimera.core.interception`): loops snapshot these headers
        into the request envelope, apply an interceptor's replacement for
        the duration of one call, and restore the snapshot afterwards.

        Returns:
            A copy of the current header map (mutating it does not
            affect the provider — assign through the setter).
        """
        return dict(self._headers)

    @request_headers.setter
    def request_headers(self, value: dict[str, str]) -> None:
        self._headers = dict(value)

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

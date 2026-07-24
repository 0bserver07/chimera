from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from chimera.providers.base import CACHE_LEVELS, Provider, Response, StreamEvent, ToolSchema
from chimera.providers.capabilities import (
    ProviderCapabilities,
    WireProtocol,
    resolve_capabilities,
)
from chimera.types import Message, ToolCall

if TYPE_CHECKING:
    from chimera.auth.manager import AuthManager

try:
    import anthropic  # type: ignore[import-not-found]
except ImportError:
    anthropic = None  # type: ignore[assignment]


def _parse_model_suffix(model: str) -> tuple[str, int | None]:
    """Split an optional ``[<window>]`` context-window suffix off a model id.

    The suffix mirrors Anthropic's ``...[1m]`` convention and lets a user
    declare the model's context window inline. It is stripped from the wire
    id -- vendors such as z.ai reject unknown ids like ``glm-5.2[1m]`` with a
    400 "Unknown Model" -- and returned as an explicit token budget so the
    loop's compaction thresholds match the model's real capacity instead of
    falling back to the 200K default.

    Examples::

        _parse_model_suffix("glm-5.2[1m]")           -> ("glm-5.2", 1_000_000)
        _parse_model_suffix("claude-sonnet-4[200k]") -> ("claude-sonnet-4", 200_000)
        _parse_model_suffix("glm-5.2")               -> ("glm-5.2", None)

    Args:
        model: Raw model id, optionally ending in ``[<number><k|m>]``.

    Returns:
        A ``(wire_model_id, context_window_or_None)`` tuple.
    """
    match = re.match(r"^(.*?)\[(\d+(?:\.\d+)?)([kKmM])\]$", model.strip())
    if match is None:
        return model, None
    base, number, unit = match.group(1), float(match.group(2)), match.group(3).lower()
    multiplier = 1_000 if unit == "k" else 1_000_000
    return base, int(number * multiplier)


class _AsyncCancelWatcher:
    """Async-side bridge from a :class:`threading.Event` to ``aclient.close()``.

    Spins a background thread (``loop.run_in_executor``) that blocks on the
    event; on set, schedules ``aclient.close()`` on the event loop so any
    in-flight async HTTP request is aborted promptly. Acts as a no-op when
    *cancel_event* is ``None``.
    """

    def __init__(
        self,
        cancel_event: threading.Event | None,
        aclient: Any,
    ) -> None:
        self._cancel_event = cancel_event
        self._aclient = aclient
        self._stop = threading.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._cancel_event is None:
            return
        loop = asyncio.get_running_loop()

        async def _runner() -> None:
            cancel_event = self._cancel_event
            assert cancel_event is not None  # checked above
            stop = self._stop
            while not stop.is_set():
                fired = await loop.run_in_executor(
                    None, lambda: cancel_event.wait(timeout=0.05),
                )
                if stop.is_set():
                    return
                if fired:
                    try:
                        await self._aclient.close()
                    except Exception:  # noqa: BLE001 - best effort
                        pass
                    return

        self._task = loop.create_task(_runner())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=0.5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._task = None


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
        cache: str = "none",
        enable_cache: bool = False,
        enable_thinking: bool = False,
        thinking_budget: int = 10_000,
        auth_manager: AuthManager | None = None,
    ) -> None:
        if anthropic is None:
            raise ImportError("pip install chimera-run[anthropic]")
        self._model, self._context_override = _parse_model_suffix(model)
        # Prompt-caching knob (see Provider docstring for the convention).
        # ``enable_cache`` is the deprecated predecessor flag: it only ever
        # cached the *static* system prompt + last tool definition. It now
        # aliases ``cache="short"`` — which additionally caches the rolling
        # last-message prefix, the real lever for agentic loops that resend
        # the full context each turn. An explicit ``cache`` wins, so
        # ``cache="long", enable_cache=True`` stays "long".
        if cache not in CACHE_LEVELS:
            raise ValueError(f"cache must be one of {CACHE_LEVELS!r}, got {cache!r}")
        if enable_cache and cache == "none":
            cache = "short"
        self._cache = cache
        self._enable_cache = enable_cache
        self._enable_thinking = enable_thinking
        self._thinking_budget = thinking_budget

        resolved_key = api_key
        if resolved_key is None and auth_manager is not None:
            try:
                resolved_key = auth_manager.get_token("anthropic")
            except Exception:
                pass
        if resolved_key is None:
            resolved_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")

        # Max-subscription OAuth tokens (sk-ant-oat01-*) authenticate via
        # Bearer, not x-api-key. The SDK accepts them via auth_token, but
        # ANTHROPIC_API_KEY in env would still poison the client with an
        # invalid x-api-key header -- pop it during construction.
        is_oauth = resolved_key is not None and resolved_key.startswith("sk-ant-oat01-")

        client_kwargs: dict[str, Any] = {}
        if is_oauth:
            client_kwargs["auth_token"] = resolved_key
            client_kwargs["api_key"] = None
            client_kwargs["default_headers"] = {"anthropic-beta": "oauth-2025-04-20"}
        else:
            client_kwargs["api_key"] = resolved_key

        if base_url or os.environ.get("ANTHROPIC_BASE_URL"):
            client_kwargs["base_url"] = base_url or os.environ.get("ANTHROPIC_BASE_URL")

        # The Anthropic SDK refuses a *non-streaming* ``messages.create`` whose
        # ``max_tokens`` could take >10 min — its guard fires for
        # ``max_tokens > ~21k`` (3600 * max_tokens / 128000 > 600), and
        # GLM/Kimi/Qwen default to 32k output here. The guard is skipped when
        # the client carries an explicit (non-default) timeout, so set one:
        # the non-streaming eval path (Harness / bench-matrix) then works for
        # these large-output models instead of raising. Callers may override
        # via ``**kw`` -> client_kwargs. See
        # ``anthropic/_base_client.py::_calculate_nonstreaming_timeout``.
        import httpx

        client_kwargs.setdefault("timeout", httpx.Timeout(900.0, connect=10.0))

        if is_oauth:
            _saved_env = os.environ.pop("ANTHROPIC_API_KEY", None)
            try:
                self._client = anthropic.Anthropic(**client_kwargs)
            finally:
                if _saved_env is not None:
                    os.environ["ANTHROPIC_API_KEY"] = _saved_env
        else:
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
        thinking: Any = None,
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
            "max_tokens": max_tokens or self._default_max_tokens,
        }

        # Resolve thinking settings: per-call param overrides instance config
        if thinking is not None:
            from chimera.providers.thinking import ThinkingLevel, budget_for_level
            enable = thinking != ThinkingLevel.OFF
            budget = budget_for_level(thinking)
        else:
            enable = self._enable_thinking
            budget = self._thinking_budget

        # Extended thinking — requires temperature=1 and uses budget_tokens
        if enable:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }
            kwargs["temperature"] = 1  # Required for extended thinking
        else:
            kwargs["temperature"] = temperature

        # Prompt caching — resolve one cache_control marker for the whole
        # request (uniform TTL, so no 5m/1h ordering constraints to worry
        # about) and attach it to the stable prefix (system + last tool) and
        # the rolling suffix (last message). ``None`` == caching disabled.
        cache_control = self._cache_control_block()

        # System message — with optional prompt caching
        if system_msg:
            if cache_control is not None:
                kwargs["system"] = [
                    {"type": "text", "text": system_msg, "cache_control": cache_control},
                ]
            else:
                kwargs["system"] = system_msg

        # Tools — with optional prompt caching on last tool definition
        if tools:
            if cache_control is not None:
                cached_tools = [*tools]
                cached_tools[-1] = {**cached_tools[-1], "cache_control": cache_control}
                kwargs["tools"] = cached_tools
            else:
                kwargs["tools"] = tools

        # Last message — the rolling breakpoint. Marking the final content
        # block means each turn reuses the previous turn's cached prefix
        # (system + tools + all prior messages) and only bills the new tail.
        if cache_control is not None:
            self._apply_cache_control_to_last_message(api_messages, cache_control)

        return kwargs

    def _cache_control_block(self) -> dict[str, Any] | None:
        """Return the ``cache_control`` marker for the active cache setting.

        ``None`` means caching is disabled. ``"short"`` → a 5-minute ephemeral
        marker; ``"long"`` → the 1-hour TTL form. Reads ``self._cache`` but
        falls back to the deprecated ``self._enable_cache`` bool when ``_cache``
        is absent, so instances built via ``__new__`` (test fixtures) or older
        callers that only set ``_enable_cache`` still behave correctly.
        """
        cache = getattr(self, "_cache", None)
        if cache is None:
            cache = "short" if getattr(self, "_enable_cache", False) else "none"
        if cache == "short":
            return {"type": "ephemeral"}
        if cache == "long":
            return {"type": "ephemeral", "ttl": "1h"}
        return None

    @staticmethod
    def _apply_cache_control_to_last_message(
        api_messages: list[dict[str, Any]],
        cache_control: dict[str, Any],
    ) -> None:
        """Attach *cache_control* to the final content block of the last message.

        Normalizes string content to a single cached text block; for list
        content (tool results, assistant tool-use turns) it copies and marks
        the last block so a caller-owned dict is never mutated in place. A
        no-op when there are no messages or the last message has empty content.
        """
        if not api_messages:
            return
        last = api_messages[-1]
        content = last.get("content")
        if isinstance(content, str):
            last["content"] = [
                {"type": "text", "text": content, "cache_control": cache_control},
            ]
        elif isinstance(content, list) and content:
            content[-1] = {**content[-1], "cache_control": cache_control}

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
        thinking: Any = None,
        cancel_event: threading.Event | None = None,
    ) -> Response:
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens, thinking=thinking)
        with self._sync_cancel_watcher(cancel_event):
            response = self._client.messages.create(**kwargs)
        return self._parse_response(response)

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream a response using the Anthropic messages stream API."""
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens, thinking=thinking)

        # Track tool call state across events
        current_tool_id: str | None = None
        current_tool_name: str | None = None
        current_tool_json = ""

        with self._sync_cancel_watcher(cancel_event), self._client.messages.stream(**kwargs) as stream:
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
                usage=self._usage_from_final(final),
            )

    @staticmethod
    def _usage_from_final(final: Any) -> dict[str, int]:
        """Build a usage dict from the final streamed message.

        Includes cache_creation/cache_read tokens when the SDK exposes
        them (they come back as zero when caching is disabled).
        """
        usage: dict[str, int] = {
            "input_tokens": final.usage.input_tokens,
            "output_tokens": final.usage.output_tokens,
        }
        cache_creation = getattr(final.usage, "cache_creation_input_tokens", None)
        cache_read = getattr(final.usage, "cache_read_input_tokens", None)
        if cache_creation is not None:
            usage["cache_creation_input_tokens"] = cache_creation
        if cache_read is not None:
            usage["cache_read_input_tokens"] = cache_read
        return usage

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
            elif delta.type == "thinking_delta":
                # Extended-thinking reasoning text: surfaced as its own event so
                # frontends can render it (collapsed by default). Never merged
                # into the assistant message content.
                yield StreamEvent(
                    type="thinking_delta", content=getattr(delta, "thinking", "") or "",
                )

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
            # Same explicit timeout as the sync client (see __init__): without
            # it the SDK's non-streaming ">10 min" guard raises for the 32k
            # default max_tokens of GLM/Kimi-class models — this async path is
            # what the assembled CodingAgent stack drives, so the eval harness
            # hit it even after the sync client was fixed.
            import httpx

            client_kwargs.setdefault("timeout", httpx.Timeout(900.0, connect=10.0))
            self._async_client = anthropic.AsyncAnthropic(**client_kwargs)  # type: ignore[union-attr]
        return self._async_client

    async def async_complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
        cancel_event: threading.Event | None = None,
    ) -> Response:
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens, thinking=thinking)
        watcher = _AsyncCancelWatcher(cancel_event, self._aclient)
        await watcher.start()
        try:
            response = await self._aclient.messages.create(**kwargs)
        finally:
            await watcher.stop()
        return self._parse_response(response)

    async def async_stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
        cancel_event: threading.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Async stream using the Anthropic async messages stream API."""
        kwargs = self._prepare_request(messages, tools, temperature, max_tokens, thinking=thinking)

        current_tool_id: str | None = None
        current_tool_name: str | None = None
        current_tool_json = ""

        watcher = _AsyncCancelWatcher(cancel_event, self._aclient)
        await watcher.start()
        try:
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
                    usage=self._usage_from_final(final),
                )
        finally:
            await watcher.stop()

    # ------------------------------------------------------------------
    # Cancellation plumbing
    # ------------------------------------------------------------------

    def _sync_cancel_watcher(self, cancel_event: threading.Event | None) -> Any:
        """Return a context manager that closes the sync httpx client on cancel.

        When *cancel_event* is ``None`` we return a no-op context manager so
        the call site stays a single ``with`` statement. When it's set, a
        background daemon thread waits on the event; if it fires we call
        ``self._client.close()`` which aborts any in-flight HTTP request,
        preempting an otherwise long-running model call.
        """
        client = self._client

        class _Watcher:
            def __enter__(self_inner) -> "_Watcher":
                self_inner._stop = threading.Event()
                if cancel_event is None:
                    self_inner._thread = None
                    return self_inner

                def _watch() -> None:
                    while not self_inner._stop.is_set():
                        if cancel_event.wait(timeout=0.05):
                            try:
                                client.close()
                            except Exception:  # noqa: BLE001 - best effort
                                pass
                            return

                t = threading.Thread(target=_watch, daemon=True)
                t.start()
                self_inner._thread = t
                return self_inner

            def __exit__(self_inner, *exc: Any) -> None:
                self_inner._stop.set()
                if self_inner._thread is not None:
                    self_inner._thread.join(timeout=0.5)

        return _Watcher()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def context_window(self) -> int:
        if self._context_override is not None:
            return self._context_override
        for prefix, size in self.CONTEXT_WINDOWS.items():
            if self._model.startswith(prefix):
                return size
        return 200_000  # Default

    @property
    def _capabilities(self) -> ProviderCapabilities:
        """Resolved Anthropic-compat capabilities for this model (matrix-sourced).

        The per-model quirks — chiefly the larger default output cap for the
        non-Claude models served over the Anthropic Messages API (GLM via
        z.ai, Kimi via Moonshot, Qwen/DeepSeek, ``z-*``) — live in the
        capability matrix as data, keyed off the model id. See
        :mod:`chimera.providers.capabilities`.
        """
        return resolve_capabilities(WireProtocol.ANTHROPIC_COMPAT, model=self._model)

    @property
    def _default_max_tokens(self) -> int:
        """Per-turn output cap when the caller doesn't pass ``max_tokens``.

        Non-Anthropic models served over this API (GLM/Kimi/Qwen via z.ai)
        support much larger outputs than Claude, so give them headroom for long
        file writes; Claude stays at a safe 8192. Sourced from the capability
        matrix (:attr:`_capabilities`) rather than a hardcoded prefix set.
        """
        return self._capabilities.default_max_tokens

    @property
    def supports_tool_use(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model


from chimera.providers.registry import register_provider as _register  # noqa: E402
_register("anthropic", lambda model="", api_key=None, base_url=None, **kw: AnthropicProvider(model=model, api_key=api_key, base_url=base_url, **kw))

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from chimera.providers.base import Provider, Response, StreamEvent, ToolSchema
from chimera.types import Message, ToolCall

if TYPE_CHECKING:
    from chimera.auth.manager import AuthManager

try:
    import anthropic  # type: ignore[import-not-found]
except ImportError:
    anthropic = None  # type: ignore[assignment]


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
        enable_cache: bool = False,
        enable_thinking: bool = False,
        thinking_budget: int = 10_000,
        auth_manager: AuthManager | None = None,
    ) -> None:
        if anthropic is None:
            raise ImportError("pip install chimera-run[anthropic]")
        self._model = model
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

        # Claude Code Max OAuth tokens (sk-ant-oat01-*) authenticate via
        # Bearer, not x-api-key. The SDK accepts them via auth_token, but
        # ANTHROPIC_API_KEY in env would still poison the client with an
        # invalid x-api-key header -- pop it during construction.
        is_oauth = bool(resolved_key) and resolved_key.startswith("sk-ant-oat01-")

        client_kwargs: dict[str, Any] = {}
        if is_oauth:
            client_kwargs["auth_token"] = resolved_key
            client_kwargs["api_key"] = None
            client_kwargs["default_headers"] = {"anthropic-beta": "oauth-2025-04-20"}
        else:
            client_kwargs["api_key"] = resolved_key

        if base_url or os.environ.get("ANTHROPIC_BASE_URL"):
            client_kwargs["base_url"] = base_url or os.environ.get("ANTHROPIC_BASE_URL")

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
            "max_tokens": max_tokens or 4096,
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
_register("anthropic", lambda model="", api_key=None, base_url=None, **kw: AnthropicProvider(model=model, api_key=api_key, base_url=base_url, **kw))

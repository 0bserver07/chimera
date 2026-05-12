# chimera/providers/ollama.py
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from chimera.providers.base import Provider, Response, StreamEvent, ToolSchema
from chimera.types import Message, ToolCall

try:
    import httpx  # type: ignore[import-not-found]
except ImportError:
    httpx = None  # type: ignore[assignment]


class _SyncOllamaCancelWatcher:
    """Context manager: closes a sync httpx.Client when *cancel_event* fires.

    Acts as a no-op when *cancel_event* is ``None``. The watcher thread
    polls every 50ms (using :meth:`threading.Event.wait` with a timeout)
    so cancel latency stays sub-100ms while never busy-looping.
    """

    def __init__(
        self,
        cancel_event: threading.Event | None,
        client: Any,
    ) -> None:
        self._cancel_event = cancel_event
        self._client = client
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_SyncOllamaCancelWatcher":
        if self._cancel_event is None:
            return self

        def _watch() -> None:
            while not self._stop.is_set():
                cancel_event = self._cancel_event
                assert cancel_event is not None
                if cancel_event.wait(timeout=0.05):
                    try:
                        self._client.close()
                    except Exception:  # noqa: BLE001 - best effort
                        pass
                    return

        t = threading.Thread(target=_watch, daemon=True)
        t.start()
        self._thread = t
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)


class _AsyncOllamaCancelWatcher:
    """Async sibling of :class:`_SyncOllamaCancelWatcher` for httpx.AsyncClient."""

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
            assert cancel_event is not None
            while not self._stop.is_set():
                fired = await loop.run_in_executor(
                    None, lambda: cancel_event.wait(timeout=0.05),
                )
                if self._stop.is_set():
                    return
                if fired:
                    try:
                        await self._aclient.aclose()
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


_DEFAULT_NUM_CTX = 131_072
_DEFAULT_KEEP_ALIVE = "60m"


class OllamaProvider(Provider):
    """Ollama local model provider.

    Talks to the native Ollama HTTP API at ``/api/chat`` (NOT the
    OpenAI-compatible ``/v1/chat/completions`` shim, which silently drops
    ``tool_calls`` from streaming chunks).

    Defaults are tuned for Kimi-K2-class tool-using models: large
    ``num_ctx``, long ``keep_alive``, and ``think: true`` enabled
    automatically when the model name starts with ``kimi``.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        context_length: int = _DEFAULT_NUM_CTX,
        keep_alive: str = _DEFAULT_KEEP_ALIVE,
        think: bool | None = None,
        api_key: str | None = None,
    ) -> None:
        if httpx is None:
            raise ImportError("pip install httpx")
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._context_length = context_length
        self._keep_alive = keep_alive
        # When unspecified, enable thinking only for kimi* models since
        # generic models reject the field.
        self._think = think if think is not None else model.lower().startswith("kimi")
        # Bearer-token auth for Ollama Cloud's direct API (https://ollama.com).
        # When unset we look at $OLLAMA_API_KEY. A local daemon ignores the
        # header, so it's safe to always send when a key is present.
        import os as _os
        self._api_key = api_key or _os.environ.get("OLLAMA_API_KEY")

    @property
    def _auth_headers(self) -> dict[str, str]:
        """Return the Authorization header dict (empty when no key)."""
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    # ------------------------------------------------------------------
    # Request / response helpers
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        temperature: float,
        max_tokens: int | None,
        stream: bool,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble the JSON body sent to ``/api/chat``.

        Args:
            messages: Conversation history.
            tools: Tool schemas (Anthropic shape) to expose, or ``None``.
            temperature: Sampling temperature.
            max_tokens: Optional ``num_predict`` cap.
            stream: Whether NDJSON streaming is requested.
            overrides: Caller-supplied kwargs that may override
                ``num_ctx``, ``keep_alive``, ``think``, or
                ``tool_choice``.

        Returns:
            Dict ready to JSON-encode for the ``/api/chat`` endpoint.
        """
        options: dict[str, Any] = {
            "temperature": temperature,
            "num_ctx": overrides.get("num_ctx", self._context_length),
        }
        if max_tokens:
            options["num_predict"] = max_tokens

        # Ollama Cloud's *direct* API (https://ollama.com) takes the bare
        # model id ("gpt-oss:120b"); the local daemon takes the "-cloud"
        # suffixed form ("gpt-oss:120b-cloud") to flag the passthrough.
        # When we're pointed straight at ollama.com, strip the suffix.
        wire_model = self._model
        if "ollama.com" in self._base_url and wire_model.endswith("-cloud"):
            wire_model = wire_model[: -len("-cloud")]

        payload: dict[str, Any] = {
            "model": wire_model,
            "messages": self._convert_messages(messages),
            "stream": stream,
            "options": options,
            "keep_alive": overrides.get("keep_alive", self._keep_alive),
        }

        think = overrides.get("think", self._think)
        if think:
            payload["think"] = True

        if tools:
            payload["tools"] = self._convert_tools(tools)
            tool_choice = overrides.get("tool_choice")
            # Server rejects tool_choice="required" alongside think:true,
            # so silently drop it in that combination.
            if tool_choice and not (think and tool_choice == "required"):
                payload["tool_choice"] = tool_choice

        return payload

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
        cancel_event: threading.Event | None = None,
        **kwargs: Any,
    ) -> Response:
        payload = self._build_payload(
            messages, tools, temperature, max_tokens, stream=False, overrides=kwargs,
        )

        if cancel_event is None:
            # Fast path — no Client allocation; matches the historical
            # behaviour and the unit-test mocks for ``httpx.post``.
            resp = httpx.post(  # type: ignore[union-attr]
                f"{self._base_url}/api/chat",
                json=payload,
                headers=self._auth_headers or None,
                timeout=300,
            )
            resp.raise_for_status()
            data = resp.json()
        else:
            # Slow path — wrap in a Client so the cancel watcher can call
            # .close() and preempt an in-flight POST. Module-level
            # httpx.post() opens its own client we can't reach into.
            client = httpx.Client(timeout=300)  # type: ignore[union-attr]
            try:
                with _SyncOllamaCancelWatcher(cancel_event, client):
                    resp = client.post(
                        f"{self._base_url}/api/chat",
                        json=payload,
                        headers=self._auth_headers or None,
                    )
                    resp.raise_for_status()
                    data = resp.json()
            finally:
                try:
                    client.close()
                except Exception:  # noqa: BLE001 - already closed in watcher
                    pass

        msg = data.get("message", {})
        content = msg.get("content", "")
        tool_calls: list[ToolCall] = []

        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:12]}",
                name=func.get("name", ""),
                arguments=args,
            ))

        return Response(
            content=content,
            tool_calls=tool_calls,
            usage={
                "input_tokens": data.get("prompt_eval_count", 0),
                "output_tokens": data.get("eval_count", 0),
            },
        )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
        cancel_event: threading.Event | None = None,
        **kwargs: Any,
    ) -> Iterator[StreamEvent]:
        """Stream a response from Ollama via NDJSON over ``/api/chat``.

        Bridges the native async generator into a sync iterator using a
        background thread + queue so synchronous callers (the ReAct
        loop's ``iter_steps``) keep working without an event loop.

        Yields:
            StreamEvent objects: ``text_delta`` for each content chunk;
            ``tool_call_start`` / ``tool_call_complete`` for each tool
            call (Ollama emits whole tool calls in one chunk, so no
            ``tool_call_delta`` events are produced); a final ``done``
            event carrying token usage.
        """
        import asyncio
        import queue
        import threading

        out: queue.Queue[StreamEvent | None | BaseException] = queue.Queue()

        def _run() -> None:
            async def _drain() -> None:
                try:
                    async for event in self.async_stream(
                        messages, tools=tools, temperature=temperature,
                        max_tokens=max_tokens, thinking=thinking,
                        cancel_event=cancel_event, **kwargs,
                    ):
                        out.put(event)
                except BaseException as exc:  # noqa: BLE001
                    out.put(exc)
                finally:
                    out.put(None)

            asyncio.run(_drain())

        threading.Thread(target=_run, daemon=True).start()

        while True:
            item = out.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    async def async_stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
        cancel_event: threading.Event | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Native async NDJSON streamer for ``/api/chat``.

        Args:
            messages: Conversation history.
            tools: Tool schemas, or ``None``.
            temperature: Sampling temperature.
            max_tokens: Optional ``num_predict`` cap.
            thinking: Ignored (Ollama uses payload-level ``think``
                derived from model name / kwargs instead).
            **kwargs: Per-request overrides for ``num_ctx``,
                ``keep_alive``, ``think``, ``tool_choice``.

        Yields:
            Stream of :class:`StreamEvent` objects matching the shapes
            consumed by ``Loop._accumulate_stream``.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses from Ollama.
        """
        payload = self._build_payload(
            messages, tools, temperature, max_tokens, stream=True, overrides=kwargs,
        )

        emitted_starts: dict[str, str] = {}
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        async with httpx.AsyncClient(timeout=None) as client:  # type: ignore[union-attr]
            watcher = _AsyncOllamaCancelWatcher(cancel_event, client)
            await watcher.start()
            try:
                async with client.stream(
                    "POST", f"{self._base_url}/api/chat", json=payload,
                    headers=self._auth_headers or None,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        msg = chunk.get("message") or {}

                        text = msg.get("content") or ""
                        if text:
                            yield StreamEvent(type="text_delta", content=text)

                        for tc in msg.get("tool_calls") or []:
                            func = tc.get("function") or {}
                            name = func.get("name", "")
                            args = func.get("arguments", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except json.JSONDecodeError:
                                    args = {}

                            # Ollama's native tool_calls have no id; key on
                            # (index, name) within this stream and synthesize
                            # a UUID reused across start/complete.
                            key = f"{len(emitted_starts)}:{name}"
                            call_id = emitted_starts.get(key)
                            if call_id is None:
                                call_id = f"call_{uuid.uuid4().hex[:12]}"
                                emitted_starts[key] = call_id
                                yield StreamEvent(
                                    type="tool_call_start",
                                    tool_call=ToolCall(id=call_id, name=name, arguments={}),
                                )

                            yield StreamEvent(
                                type="tool_call_complete",
                                tool_call=ToolCall(id=call_id, name=name, arguments=args),
                            )

                        if chunk.get("done"):
                            usage = {
                                "input_tokens": chunk.get("prompt_eval_count", 0),
                                "output_tokens": chunk.get("eval_count", 0),
                            }
                            break
            finally:
                await watcher.stop()

        yield StreamEvent(type="done", usage=usage)

    # ------------------------------------------------------------------
    # Message / tool conversion
    # ------------------------------------------------------------------

    def _convert_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert internal :class:`Message` objects to Ollama wire shape.

        Tool-result messages carry ``tool_name`` (resolved by walking
        back to the matching prior assistant ``tool_calls`` entry),
        which Ollama requires in lieu of an OpenAI-style
        ``tool_call_id``.
        """
        # Build id -> name map from preceding assistant tool_calls
        # so tool results can be tagged with tool_name.
        id_to_name: dict[str, str] = {}
        for msg in messages:
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    id_to_name[tc.id] = tc.name

        api_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                entry: dict[str, Any] = {
                    "role": "tool",
                    "content": msg.content,
                }
                if msg.call_id and msg.call_id in id_to_name:
                    entry["tool_name"] = id_to_name[msg.call_id]
                api_messages.append(entry)
            elif msg.role == "assistant" and msg.tool_calls:
                tc_list = []
                for tc in msg.tool_calls:
                    tc_list.append({
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                    })
                api_messages.append({
                    "role": "assistant",
                    "content": msg.content,
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


def _ollama_factory(
    model: str = "",
    base_url: str | None = None,
    api_key: str | None = None,
    **kw: Any,
) -> OllamaProvider:
    # Resolution order: explicit base_url > $OLLAMA_HOST > local daemon default.
    # Users running Ollama Cloud (https://ollama.com) or a remote/private
    # Ollama instance set OLLAMA_HOST to their endpoint; only when nothing
    # is set do we fall through to the local daemon assumption.
    import os
    resolved = base_url or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
    # Bare hostnames (e.g. "ollama.com") need a scheme — assume https for
    # anything that isn't already a full URL.
    if resolved and not resolved.startswith(("http://", "https://")):
        resolved = f"https://{resolved}"
    # api_key threads through to the Authorization: Bearer header — used by
    # Ollama Cloud's direct API. Falls back to $OLLAMA_API_KEY inside the
    # provider when None. Strip from kw if a caller already passed it.
    kw.pop("api_key", None)
    return OllamaProvider(model=model, base_url=resolved, api_key=api_key, **kw)


_register("ollama", _ollama_factory)

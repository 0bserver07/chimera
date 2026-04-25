"""WebSocket transport for MCP servers.

Uses the optional ``websockets`` library (install via the ``mcp`` extra:
``uv pip install chimera-run[mcp]``). If the library is missing, importing
or instantiating :class:`WebSocketTransport` raises a clear error so the
core install stays dependency-free.

A single asyncio event loop runs in a background thread; each ``send``
call schedules a JSON-RPC roundtrip on it and blocks the caller until the
response arrives (matching the synchronous interface of
:class:`~chimera.mcp.transport.MCPTransport`).
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from chimera.mcp.transport import MCPTransport

try:  # pragma: no cover - import-guarded
    import websockets  # type: ignore[import-not-found]
    _WS_AVAILABLE = True
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]
    _WS_AVAILABLE = False


class WebSocketTransport(MCPTransport):
    """JSON-RPC 2.0 over a single persistent WebSocket connection.

    Args:
        url: ``ws://`` or ``wss://`` endpoint.
        headers: Optional headers (e.g. ``Authorization``).
        timeout: Per-request timeout in seconds.

    Raises:
        ImportError: When the ``websockets`` extra is not installed.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not _WS_AVAILABLE:
            raise ImportError(
                "WebSocketTransport requires the 'websockets' package. "
                "Install via: pip install chimera-run[mcp]"
            )
        self._url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._ws: Any = None
        self._pending: dict[int | str, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None

    # ---- lifecycle -------------------------------------------------

    def start(self) -> None:
        """Spin up the background event loop and connect the socket."""
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()
        self._ready.wait(timeout=self._timeout)
        if self._start_error is not None:
            raise ConnectionError(f"WebSocket connect failed: {self._start_error}")
        if self._ws is None:
            raise ConnectionError("WebSocket failed to start within timeout")

    def close(self) -> None:
        """Close the socket and stop the loop."""
        if self._loop is None:
            return
        loop = self._loop

        async def _shutdown() -> None:
            if self._reader_task is not None:
                self._reader_task.cancel()
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:
                    pass

        try:
            fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            fut.result(timeout=2.0)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=2.0)
        self._loop = None
        self._loop_thread = None
        self._ws = None

    # ---- send ------------------------------------------------------

    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Send a JSON-RPC message; block for the matching response."""
        if self._loop is None or self._ws is None:
            raise ConnectionError("WebSocketTransport not started")
        msg_id = message.get("id")
        loop = self._loop

        async def _send_and_wait() -> dict[str, Any] | None:
            data = json.dumps(message)
            if msg_id is None:
                await self._ws.send(data)
                return None
            fut: asyncio.Future[dict[str, Any]] = loop.create_future()
            self._pending[msg_id] = fut
            try:
                await self._ws.send(data)
                return await asyncio.wait_for(fut, timeout=self._timeout)
            finally:
                self._pending.pop(msg_id, None)

        coro_fut = asyncio.run_coroutine_threadsafe(_send_and_wait(), loop)
        try:
            return coro_fut.result(timeout=self._timeout + 1.0)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"No WS response for request id={msg_id} within {self._timeout}s"
            ) from exc

    # ---- internal --------------------------------------------------

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._connect())
            if self._ws is not None:
                self._reader_task = loop.create_task(self._reader())
                loop.run_forever()
        except BaseException as exc:  # noqa: BLE001 - surface to start()
            self._start_error = exc
            self._ready.set()
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _connect(self) -> None:
        try:
            kwargs: dict[str, Any] = {}
            if self._headers:
                # websockets >= 12 uses ``additional_headers`` (was ``extra_headers``).
                kwargs["additional_headers"] = list(self._headers.items())
            self._ws = await websockets.connect(self._url, **kwargs)  # type: ignore[union-attr]
        except Exception as exc:
            self._start_error = exc
        finally:
            self._ready.set()

    async def _reader(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                msg_id = payload.get("id")
                if msg_id is None:
                    continue
                fut = self._pending.get(msg_id)
                if fut is not None and not fut.done():
                    fut.set_result(payload)
        except asyncio.CancelledError:
            return
        except Exception:
            return

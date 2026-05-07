"""JSON-RPC 2.0 stdio server for ``chimera weasel --mode rpc``.

This module implements the wire protocol described by the JSON-RPC 2.0 spec
(<https://www.jsonrpc.org/specification>) over newline-delimited stdin /
stdout. It exposes four methods:

* ``prompt(message: str) -> {"output": str, "success": bool}``
* ``cancel() -> {"cancelled": bool}``
* ``get_state() -> {"messages": [...], "model": str}``
* ``list_models() -> {"models": [...]}``

The server composes :class:`chimera.rpc.handler.RpcHandler` when a Chimera
:class:`~chimera.sessions.session.Session` is supplied (full agent mode),
and otherwise falls back to a tiny in-process stub useful for tests and
extension authors. Either way the wire protocol is identical.

Example:
    Spawn the server in a subprocess and round-trip a single call::

        $ chimera weasel --mode rpc <<EOF
        {"jsonrpc":"2.0","id":1,"method":"list_models"}
        EOF
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, TextIO

# JSON-RPC 2.0 standard error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class WeaselRpcServer:
    """JSON-RPC 2.0 server bound to stdin/stdout.

    The server reads one JSON-RPC request per line, dispatches to a method
    handler, and writes the JSON-RPC response (also single-line) to stdout.
    Notifications (requests with no ``id``) are processed silently.

    Args:
        session: Optional Chimera :class:`~chimera.sessions.session.Session`.
            When provided, ``prompt``/``cancel``/``get_state`` are wired
            through :class:`chimera.rpc.handler.RpcHandler`.  When
            ``None``, the server falls back to a minimal in-memory stub
            so tests and extension authors can drive the wire protocol
            without spinning up a full agent.
        stdin: Input stream (defaults to :data:`sys.stdin`).
        stdout: Output stream (defaults to :data:`sys.stdout`).
        list_models: Optional zero-arg callable returning the list of model
            identifiers to expose via the ``list_models`` method.  Defaults
            to :func:`_default_list_models`.
    """

    def __init__(
        self,
        session: Any | None = None,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        list_models: Callable[[], list[str]] | None = None,
    ) -> None:
        self._session = session
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._list_models = list_models or _default_list_models
        self._cancelled = False
        self._stub_messages: list[dict[str, str]] = []
        self._stub_model: str = ""

        # Compose the canonical handler when we have a real session.
        self._chimera_handler: Any | None = None
        if session is not None:
            try:
                from chimera.rpc.handler import RpcHandler
                # The handler reads/writes via server._session and server._emit;
                # we satisfy that contract with a lightweight shim.
                self._chimera_handler = RpcHandler(_HandlerShim(self))
            except Exception:
                # Fall back to stub mode if the handler cannot be composed.
                self._chimera_handler = None

        self._methods: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "prompt": self._method_prompt,
            "cancel": self._method_cancel,
            "get_state": self._method_get_state,
            "list_models": self._method_list_models,
        }

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    def run(self) -> int:
        """Read JSON-RPC requests from stdin until EOF.

        Returns:
            Process exit code (always ``0``; protocol errors are reported
            on stdout per the JSON-RPC 2.0 spec, not as exit codes).
        """
        for raw_line in self._stdin:
            line = raw_line.strip()
            if not line:
                continue
            self._handle_line(line)
        return 0

    # ------------------------------------------------------------------
    # Internal: per-line dispatch
    # ------------------------------------------------------------------

    def _handle_line(self, line: str) -> None:
        """Parse a single JSON-RPC frame and dispatch.

        Args:
            line: A single newline-stripped frame from stdin.
        """
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            self._write_error(None, PARSE_ERROR, "Parse error")
            return

        # Batch requests are valid JSON-RPC 2.0 but out of scope here.
        if not isinstance(request, dict):
            self._write_error(None, INVALID_REQUEST, "Invalid Request")
            return

        if request.get("jsonrpc") != "2.0":
            self._write_error(
                request.get("id"), INVALID_REQUEST,
                "Invalid Request: jsonrpc must be '2.0'",
            )
            return

        method = request.get("method")
        if not isinstance(method, str):
            self._write_error(
                request.get("id"), INVALID_REQUEST,
                "Invalid Request: missing method",
            )
            return

        params = request.get("params") or {}
        if not isinstance(params, dict):
            self._write_error(
                request.get("id"), INVALID_PARAMS,
                "Invalid params: must be an object",
            )
            return

        handler = self._methods.get(method)
        if handler is None:
            self._write_error(
                request.get("id"), METHOD_NOT_FOUND,
                f"Method not found: {method}",
            )
            return

        try:
            result = handler(params)
        except _RpcError as e:
            self._write_error(request.get("id"), e.code, e.message, e.data)
            return
        except Exception as e:
            self._write_error(
                request.get("id"), INTERNAL_ERROR, f"Internal error: {e}",
            )
            return

        # Notifications (id is absent or null) get no response.
        if "id" not in request or request.get("id") is None:
            return
        self._write_result(request["id"], result)

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def _method_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle ``prompt`` — send a user message to the agent.

        Args:
            params: Must contain ``message``: str. May also contain
                ``stream``: bool (default ``False``); when ``True`` the
                server emits one ``stream/event`` JSON-RPC notification
                per agent step before writing the final response.

        Returns:
            ``{"output": str, "success": bool}``.

        Raises:
            _RpcError: If ``message`` is missing or not a string.
        """
        message = params.get("message")
        if not isinstance(message, str):
            raise _RpcError(INVALID_PARAMS, "params.message must be a string")
        stream_raw = params.get("stream", False)
        stream = bool(stream_raw)
        self._cancelled = False

        if self._session is not None:
            if stream:
                return self._prompt_streaming(message)
            try:
                result = self._session.chat(message)
                output = getattr(result, "output", "") or ""
                success = bool(getattr(result, "success", True))
                return {"output": output, "success": success}
            except Exception as e:
                return {"output": "", "success": False, "error": str(e)}

        # Stub fallback — useful for tests and bare extension authors.
        self._stub_messages.append({"role": "user", "content": message})
        echo = f"echo: {message}"
        self._stub_messages.append({"role": "assistant", "content": echo})
        if stream:
            # Synthesize a single-step stream so clients can exercise the
            # notification path without spinning up a real session.
            self._write_notification(
                "stream/event",
                {
                    "kind": "step",
                    "step": 0,
                    "done": True,
                    "content": echo,
                    "tool_calls": [],
                },
            )
            self._write_notification(
                "stream/event",
                {"kind": "done", "output": echo, "success": True},
            )
        return {"output": echo, "success": True}

    def _prompt_streaming(self, message: str) -> dict[str, Any]:
        """Drive ``session.iter_chat`` and emit per-step notifications.

        Each step yielded by :meth:`Session.iter_chat` is wrapped in a
        ``stream/event`` notification with ``kind == "step"``. After the
        generator completes, a final ``stream/event`` notification with
        ``kind == "done"`` is emitted, followed by the normal JSON-RPC
        response carrying the final ``AgentResult`` summary.

        Args:
            message: User message to send.

        Returns:
            The standard ``prompt`` envelope: ``{"output", "success"}``.
        """
        sess = self._session
        # Defensive: caller already gated on session is not None, but
        # mypy / runtime safety want an explicit check.
        if sess is None or not hasattr(sess, "iter_chat"):
            try:
                result = sess.chat(message) if sess is not None else None
            except Exception as e:
                return {"output": "", "success": False, "error": str(e)}
            output = getattr(result, "output", "") if result is not None else ""
            success = bool(getattr(result, "success", True)) if result is not None else False
            return {"output": output or "", "success": success}

        last_output = ""
        success = True
        error: str | None = None
        try:
            generator = sess.iter_chat(message)
            while True:
                try:
                    step = next(generator)
                except StopIteration as stop:
                    final = stop.value
                    if final is not None:
                        last_output = getattr(final, "output", "") or ""
                        success = bool(getattr(final, "success", True))
                    break
                self._write_notification(
                    "stream/event",
                    _step_to_payload(step),
                )
                # Cooperative cancel: if the client called ``cancel``
                # mid-stream, we stop pulling steps. The session's own
                # cancel hook still flags the underlying loop.
                if self._cancelled:
                    if hasattr(sess, "cancel"):
                        try:
                            sess.cancel()
                        except Exception:
                            pass
                    success = False
                    error = "cancelled"
                    break
        except Exception as e:
            success = False
            error = str(e)

        done_payload: dict[str, Any] = {
            "kind": "done",
            "output": last_output,
            "success": success,
        }
        if error is not None:
            done_payload["error"] = error
        self._write_notification("stream/event", done_payload)

        envelope: dict[str, Any] = {"output": last_output, "success": success}
        if error is not None:
            envelope["error"] = error
        return envelope

    def _method_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle ``cancel`` — cancel the in-flight turn (best effort).

        Args:
            params: Ignored.

        Returns:
            ``{"cancelled": bool}``.
        """
        self._cancelled = True
        if self._session is not None and hasattr(self._session, "cancel"):
            try:
                self._session.cancel()
            except Exception:
                pass
        return {"cancelled": True}

    def _method_get_state(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle ``get_state`` — return current conversation + model.

        Args:
            params: Ignored.

        Returns:
            ``{"messages": [...], "model": str}``.
        """
        if self._session is not None:
            messages = []
            for m in getattr(self._session, "messages", []):
                role = getattr(m, "role", None)
                content = getattr(m, "content", None)
                if role is None or content is None:
                    continue
                messages.append({"role": role, "content": content})
            agent = getattr(self._session, "_agent", None)
            provider = getattr(agent, "provider", None) if agent else None
            model = getattr(provider, "model_name", "") if provider else ""
            return {"messages": messages, "model": model}

        return {
            "messages": list(self._stub_messages),
            "model": self._stub_model,
        }

    def _method_list_models(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle ``list_models`` — enumerate models known to weasel.

        Args:
            params: Ignored.

        Returns:
            ``{"models": list[str]}``.
        """
        try:
            models = list(self._list_models())
        except Exception as e:
            raise _RpcError(INTERNAL_ERROR, f"list_models failed: {e}") from e
        return {"models": models}

    # ------------------------------------------------------------------
    # Wire I/O
    # ------------------------------------------------------------------

    def _write_result(self, request_id: Any, result: dict[str, Any]) -> None:
        """Write a JSON-RPC 2.0 success response to stdout."""
        payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        self._stdout.write(json.dumps(payload) + "\n")
        self._stdout.flush()

    def _write_error(
        self,
        request_id: Any,
        code: int,
        message: str,
        data: Any = None,
    ) -> None:
        """Write a JSON-RPC 2.0 error response to stdout."""
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        payload = {"jsonrpc": "2.0", "id": request_id, "error": err}
        self._stdout.write(json.dumps(payload) + "\n")
        self._stdout.flush()

    def _write_notification(self, method: str, params: dict[str, Any]) -> None:
        """Write a JSON-RPC 2.0 notification (no id, no response expected).

        Used by the streaming ``prompt`` path to emit one frame per
        agent step before the final response is written. Notifications
        are valid JSON-RPC 2.0 frames that the client must accept
        without sending back an envelope of their own.
        """
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self._stdout.write(json.dumps(payload) + "\n")
        self._stdout.flush()


def _step_to_payload(step: Any) -> dict[str, Any]:
    """Marshal a :class:`StepResult` (or duck type) into a notification body.

    Reads only the public, non-load-bearing fields so any object shaped
    like ``StepResult`` works — keeps the rpc module decoupled from the
    rest of the agent runtime.
    """
    message = getattr(step, "message", None)
    content = getattr(message, "content", "") if message is not None else ""
    role = getattr(message, "role", "") if message is not None else ""
    raw_calls = getattr(step, "tool_calls", []) or []
    tool_calls: list[dict[str, Any]] = []
    for tc in raw_calls:
        tool_calls.append(
            {
                "id": getattr(tc, "id", ""),
                "name": getattr(tc, "name", ""),
                "arguments": getattr(tc, "arguments", {}) or {},
            }
        )
    return {
        "kind": "step",
        "step": int(getattr(step, "step", 0) or 0),
        "done": bool(getattr(step, "done", False)),
        "role": role or "",
        "content": content or "",
        "tool_calls": tool_calls,
        "cost": float(getattr(step, "cost", 0.0) or 0.0),
    }


# ---------------------------------------------------------------------------
# Entry point used by the CLI dispatcher
# ---------------------------------------------------------------------------


def run_rpc_server(args: Any) -> int:
    """Build a :class:`WeaselRpcServer` from CLI args and run it.

    The function is a thin glue layer over :class:`WeaselRpcServer`.run so
    that the CLI / mode dispatcher can stay declarative. When a Chimera
    session cannot be constructed (missing API keys, etc.), the server
    runs in stub mode rather than failing — this keeps ``--mode rpc``
    useful for editor integrations that just want method discovery.

    Args:
        args: Parsed CLI args. May supply ``model`` / ``workdir``.

    Returns:
        Process exit code from the run loop.
    """
    session = _try_build_session(args)
    server = WeaselRpcServer(session=session)
    return server.run()


def _try_build_session(args: Any) -> Any | None:
    """Attempt to construct a Chimera session for *args*.

    Falls back to ``None`` (stub mode) if any required component is
    missing. Errors are swallowed silently so the RPC server starts in
    a usable state even when credentials are absent.

    Args:
        args: Parsed CLI args.

    Returns:
        A :class:`~chimera.sessions.session.Session` or ``None``.
    """
    try:
        import os
        from chimera.core.agent import Agent
        from chimera.core.loop import ReAct
        from chimera.core.prompt import Prompt
        from chimera.env.local import LocalEnvironment
        from chimera.providers.factory import create_provider
        from chimera.sessions.session import Session
        from chimera.weasel.providers import build_provider
    except ImportError:
        return None

    try:
        workdir = os.path.abspath(
            getattr(args, "workdir", None)
            or getattr(args, "cwd", None)
            or os.getcwd()
        )
        # WHY: route through the weasel chain so Ollama-tagged model ids
        # (``glm-5.1:cloud``) and OpenRouter / llama.cpp fallbacks all
        # work. The bare ``create_provider`` factory only does
        # prefix-based inference — it would route ``glm-5.1:cloud`` to
        # Anthropic and fail. Fall back to ``create_provider`` only when
        # the weasel chain raises (e.g. caller passed an Anthropic id
        # directly without setting OLLAMA_API_KEY).
        try:
            provider = build_provider(args)
        except Exception:
            provider = create_provider(model=getattr(args, "model", None))
        env = LocalEnvironment(workdir=workdir)
        env.setup()
        loop = ReAct(max_steps=int(getattr(args, "max_steps", 50) or 50))
        prompt = Prompt.from_string("You are a minimal Chimera coding agent.")
        agent = Agent(provider=provider, tools=[], loop=loop, prompt=prompt)
        return Session(agent=agent, env=env)
    except Exception:
        return None


def _default_list_models() -> list[str]:
    """Return the catalog-known model identifiers (best-effort).

    Returns:
        Sorted list of model identifiers; empty list on failure.
    """
    try:
        from chimera.providers.catalog import ProviderCatalog
        return sorted(ProviderCatalog.default().models)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


class _RpcError(Exception):
    """Internal — raised by method handlers to map to a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class _HandlerShim:
    """Adapter exposing the surface :class:`RpcHandler` expects.

    :class:`chimera.rpc.handler.RpcHandler` reads ``self._server._session``
    and writes via ``self._server._emit``. We keep a session pointer and
    discard emitted events (the wire format is different here, so we drive
    everything through the request/response cycle instead).

    Args:
        outer: The owning :class:`WeaselRpcServer`.
    """

    def __init__(self, outer: WeaselRpcServer) -> None:
        self._outer = outer

    @property
    def _session(self) -> Any:
        return self._outer._session

    def _emit(self, event_or_response: Any) -> None:  # noqa: D401 - shim
        # Intentional no-op: weasel emits via the JSON-RPC 2.0 envelope,
        # not the chimera.rpc event stream.
        return None


__all__ = [
    "WeaselRpcServer",
    "run_rpc_server",
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
]

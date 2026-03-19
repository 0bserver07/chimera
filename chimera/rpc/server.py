"""stdin/stdout JSON-RPC server for headless agent control."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any, Callable, TextIO

from chimera.rpc.types import (
    CancelCommand,
    CompactCommand,
    ErrorEvent,
    GetStateCommand,
    PromptCommand,
    RpcCommand,
    RpcEvent,
    RpcResponse,
    SetModelCommand,
    SteerCommand,
)

_COMMAND_MAP: dict[str, type] = {
    "prompt": PromptCommand,
    "steer": SteerCommand,
    "cancel": CancelCommand,
    "get_state": GetStateCommand,
    "compact": CompactCommand,
    "set_model": SetModelCommand,
}


class RpcServer:
    """JSON-line RPC server over stdin/stdout.

    Each newline-delimited JSON object on stdin is parsed as an
    :class:`~chimera.rpc.types.RpcCommand`, dispatched to the
    registered handler, and the handler writes responses or events back
    to stdout via :meth:`_emit`.

    Args:
        session: Active :class:`~chimera.sessions.session.Session` instance.
        stdin: Input stream (defaults to :data:`sys.stdin`).
        stdout: Output stream (defaults to :data:`sys.stdout`).
    """

    def __init__(
        self,
        session: Any,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        self._session = session
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._handlers: dict[str, Callable] = {}

    def set_handlers(self, handlers: dict[str, Callable]) -> None:
        """Register command-type → callable mapping.

        Args:
            handlers: Mapping from command type string to handler callable.
        """
        self._handlers = handlers

    def run(self) -> None:
        """Read lines from stdin and dispatch commands until EOF."""
        for line in self._stdin:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                command = self._parse_command(raw)
                self._dispatch(command)
            except json.JSONDecodeError as e:
                self._emit(ErrorEvent(message=f"Invalid JSON: {e}"))
            except Exception as e:
                self._emit(ErrorEvent(message=str(e)))

    def _parse_command(self, raw: dict[str, Any]) -> RpcCommand:
        """Deserialise a raw dict into a typed :class:`RpcCommand`.

        Unknown command types fall back to the base :class:`RpcCommand`.

        Args:
            raw: Decoded JSON dict from stdin.

        Returns:
            Typed command instance.
        """
        import dataclasses

        cmd_type = raw.get("type", "")
        cls = _COMMAND_MAP.get(cmd_type, RpcCommand)
        valid_fields = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in raw.items() if k in valid_fields}
        return cls(**filtered)

    def _dispatch(self, command: RpcCommand) -> None:
        """Look up and invoke the handler for *command*.

        If no handler is registered, emits an error :class:`RpcResponse`.

        Args:
            command: Parsed command to dispatch.
        """
        handler = self._handlers.get(command.type)
        if handler is None:
            self._emit(
                RpcResponse(
                    command=command.type,
                    id=command.id,
                    success=False,
                    error=f"Unknown command: {command.type}",
                )
            )
            return
        handler(command)

    def _emit(self, event_or_response: RpcEvent | RpcResponse) -> None:
        """Serialise *event_or_response* as a JSON line on stdout.

        Args:
            event_or_response: Outbound message to write.
        """
        self._stdout.write(json.dumps(asdict(event_or_response)) + "\n")
        self._stdout.flush()

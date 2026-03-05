"""JSON-RPC 2.0 client over subprocess stdio for ACP."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Any, Callable

from chimera.acp.types import ACPResponse, ACPSessionConfig, ACPToolCall


class ACPClient:
    """JSON-RPC 2.0 client that communicates with an ACP server over stdio.

    Args:
        config: Session configuration including command to spawn.
    """

    def __init__(self, config: ACPSessionConfig) -> None:
        self.config = config
        self._process: subprocess.Popen[bytes] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._session_id: str | None = None

    def start(self) -> None:
        """Spawn the ACP server subprocess and create a session."""
        cmd = self.config.command + self.config.args
        env = {**os.environ, **self.config.env}
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=self.config.working_dir,
        )
        result = self._rpc("session/create", {
            "working_dir": self.config.working_dir or ".",
        })
        self._session_id = result["session_id"]

    def send_message(
        self, text: str, on_chunk: Callable[[str], None] | None = None,
    ) -> ACPResponse:
        """Send a message and collect the full response.

        Args:
            text: Message text to send to the external agent.
            on_chunk: Optional callback for streaming text chunks.

        Returns:
            An :class:`ACPResponse` with accumulated text, thoughts, and tool calls.
        """
        accumulated_text: list[str] = []
        accumulated_thoughts: list[str] = []
        accumulated_tool_calls: list[ACPToolCall] = []
        total_cost = 0.0
        input_tokens = 0
        output_tokens = 0

        def handle_notification(notification: dict[str, Any]) -> None:
            nonlocal total_cost, input_tokens, output_tokens
            method = notification.get("method", "")
            params = notification.get("params", {})

            if method == "agent/messageChunk":
                chunk = params.get("text", "")
                accumulated_text.append(chunk)
                if on_chunk:
                    on_chunk(chunk)
            elif method == "agent/thoughtChunk":
                accumulated_thoughts.append(params.get("text", ""))
            elif method == "agent/toolCallStart":
                accumulated_tool_calls.append(ACPToolCall(
                    tool_call_id=params.get("tool_call_id", ""),
                    title=params.get("title", ""),
                    tool_kind=params.get("tool_kind", ""),
                    status="running",
                ))
            elif method == "agent/toolCallComplete":
                for tc in accumulated_tool_calls:
                    if tc.tool_call_id == params.get("tool_call_id"):
                        tc.status = "completed"
                        tc.raw_output = params.get("output")
                        tc.is_error = params.get("is_error", False)
            elif method == "agent/usageUpdate":
                total_cost = params.get("total_cost", total_cost)
                input_tokens = params.get("input_tokens", input_tokens)
                output_tokens = params.get("output_tokens", output_tokens)

        self._rpc("session/sendMessage", {
            "session_id": self._session_id,
            "message": text,
        }, notification_handler=handle_notification)

        return ACPResponse(
            text="".join(accumulated_text),
            thoughts=accumulated_thoughts,
            tool_calls=accumulated_tool_calls,
            cost=total_cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def fork_session(self) -> str:
        """Fork current session for parallel queries.

        Returns:
            The new session ID.
        """
        result = self._rpc("session/fork", {
            "session_id": self._session_id,
        })
        return result["session_id"]

    def stop(self) -> None:
        """Terminate the subprocess."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        notification_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and return the result.

        Args:
            method: RPC method name.
            params: RPC parameters.
            notification_handler: Optional callback for interleaved notifications.

        Returns:
            The ``result`` field from the JSON-RPC response.

        Raises:
            RuntimeError: If the process is not running or RPC returns an error.
        """
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise RuntimeError("ACP process not started")

        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            self._write(json.dumps(request))

            while True:
                line = self._readline()
                if not line:
                    raise RuntimeError("ACP process closed stdout")
                msg = json.loads(line)
                if "id" in msg and msg["id"] == request_id:
                    if "error" in msg:
                        raise RuntimeError(
                            f"ACP RPC error: {msg['error']}"
                        )
                    return msg.get("result", {})
                elif "method" in msg and notification_handler:
                    notification_handler(msg)

    def _write(self, data: str) -> None:
        assert self._process and self._process.stdin
        self._process.stdin.write((data + "\n").encode())
        self._process.stdin.flush()

    def _readline(self) -> str:
        assert self._process and self._process.stdout
        return self._process.stdout.readline().decode().strip()

    def __enter__(self) -> ACPClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

"""Tests for :mod:`chimera.otter.acp` — the ACP server transport.

These tests drive :class:`OtterACPServer` with synthetic JSON-RPC frames
fed through in-memory reader/writer fakes. The agent itself is mocked so
no provider / network / filesystem work is required.

We verify:

* ``initialize`` returns a protocol-compliant capability bag.
* Methods other than ``initialize`` are rejected before ``initialize``.
* ``session/new`` allocates a session bound to ``cwd``.
* ``session/message`` runs the wrapped agent and emits ``session/update``
  notifications around the turn (``turn_start`` → message chunk →
  ``turn_end``).
* ``session/cancel`` flips the session's cancel event and a long-running
  agent drains with ``stopReason == "cancelled"``.
* ``tool/approve`` resolves a pending permission future.
* Unknown methods produce a JSON-RPC ``-32601`` reply.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from chimera.otter.acp import (
    OTTER_ACP_AGENT_NAME,
    OTTER_ACP_PROTOCOL_VERSION,
    ACPSessionState,
    OtterACPServer,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeReader:
    """In-memory ``_LineReader`` fed by ``feed`` / ``close``."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._closed = False

    async def feed(self, line: str) -> None:
        await self._queue.put(line.encode("utf-8") + b"\n")

    async def close(self) -> None:
        self._closed = True
        await self._queue.put(b"")

    async def readline(self) -> bytes:
        return await self._queue.get()


class _FakeWriter:
    """In-memory ``_LineWriter`` capturing every JSON object written."""

    def __init__(self) -> None:
        self.lines: list[bytes] = []
        self._event = asyncio.Event()

    async def write(self, data: bytes) -> None:
        # Frames are newline-delimited; one ``write`` may carry one frame.
        for chunk in data.splitlines():
            if chunk.strip():
                self.lines.append(chunk)
        self._event.set()

    def messages(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for line in self.lines:
            out.append(json.loads(line))
        return out

    async def wait_for(self, predicate: Any, timeout: float = 1.0) -> dict[str, Any]:
        """Wait until ``predicate(msg)`` matches a captured message."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for msg in self.messages():
                if predicate(msg):
                    return msg
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError("predicate never matched")
            self._event.clear()
            try:
                await asyncio.wait_for(self._event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                continue


class _MockAgent:
    """Mock agent recording ``async_run`` calls and yielding canned output."""

    def __init__(
        self,
        *,
        output: str = "ok",
        success: bool = True,
        delay: float = 0.0,
        error: str | None = None,
    ) -> None:
        self.output = output
        self.success = success
        self.delay = delay
        self.error = error
        self.calls: list[str] = []

    async def async_run(self, task: str, env: Any | None) -> Any:
        self.calls.append(task)
        if self.delay:
            await asyncio.sleep(self.delay)

        class _Result:
            pass

        r = _Result()
        r.output = self.output  # type: ignore[attr-defined]
        r.success = self.success  # type: ignore[attr-defined]
        r.error = self.error  # type: ignore[attr-defined]
        r.steps = 1  # type: ignore[attr-defined]
        return r


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _drive_server(
    server: OtterACPServer,
    reader: _FakeReader,
) -> asyncio.Task[None]:
    """Spawn ``serve_forever`` and return its task."""
    task = asyncio.create_task(server.serve_forever())
    # Yield once so the read loop is parked on ``readline``.
    await asyncio.sleep(0)
    return task


def _request(method: str, params: Any, *, request_id: int = 1) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    )


def _notify(method: str, params: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": params})


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_returns_capabilities() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("initialize", {"protocolVersion": 1}))
    reply = await writer.wait_for(lambda m: m.get("id") == 1)

    assert reply["result"]["protocolVersion"] == OTTER_ACP_PROTOCOL_VERSION
    assert reply["result"]["agentInfo"]["name"] == OTTER_ACP_AGENT_NAME
    caps = reply["result"]["agentCapabilities"]
    assert caps["promptCapabilities"]["text"] is True
    assert caps["sessionCapabilities"]["cancel"] is True
    assert caps["toolApproval"] is True
    assert server.initialized is True

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_method_before_initialize_errors() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("session/new", {"cwd": "."}, request_id=7))
    reply = await writer.wait_for(lambda m: m.get("id") == 7)
    assert "error" in reply
    assert reply["error"]["code"] == -32002

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# session/new
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_new_allocates_session() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("initialize", {}, request_id=1))
    await writer.wait_for(lambda m: m.get("id") == 1)

    await reader.feed(_request("session/new", {"cwd": "/tmp/x"}, request_id=2))
    reply = await writer.wait_for(lambda m: m.get("id") == 2)

    sid = reply["result"]["sessionId"]
    assert sid.startswith("otter-")
    assert reply["result"]["cwd"] == "/tmp/x"
    assert sid in server.sessions
    assert server.sessions[sid].working_dir == "/tmp/x"

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# session/message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_message_streams_updates_and_returns() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    mock = _MockAgent(output="hello world")
    server = OtterACPServer(
        agent_factory=lambda _state: mock,
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("initialize", {}, request_id=1))
    await writer.wait_for(lambda m: m.get("id") == 1)

    await reader.feed(_request("session/new", {"cwd": "."}, request_id=2))
    reply = await writer.wait_for(lambda m: m.get("id") == 2)
    sid = reply["result"]["sessionId"]

    await reader.feed(
        _request(
            "session/message",
            {"sessionId": sid, "message": "do the thing"},
            request_id=3,
        )
    )
    final = await writer.wait_for(lambda m: m.get("id") == 3)

    assert final["result"]["stopReason"] == "end_turn"
    assert final["result"]["output"] == "hello world"
    assert mock.calls == ["do the thing"]

    msgs = writer.messages()
    notifs = [
        m for m in msgs
        if m.get("method") == "session/update"
        and m.get("params", {}).get("sessionId") == sid
    ]
    kinds = [n["params"]["update"]["sessionUpdate"] for n in notifs]
    assert "turn_start" in kinds
    assert "agent_message_chunk" in kinds
    assert "turn_end" in kinds

    chunk = next(
        n for n in notifs
        if n["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    )
    assert chunk["params"]["update"]["content"]["text"] == "hello world"

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_session_message_unknown_session_errors() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("initialize", {}, request_id=1))
    await writer.wait_for(lambda m: m.get("id") == 1)

    await reader.feed(
        _request(
            "session/message",
            {"sessionId": "nope", "message": "x"},
            request_id=2,
        )
    )
    reply = await writer.wait_for(lambda m: m.get("id") == 2)
    assert "error" in reply
    assert reply["error"]["code"] == -32602

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# session/cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_cancel_aborts_active_turn() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    mock = _MockAgent(output="never seen", delay=5.0)
    server = OtterACPServer(
        agent_factory=lambda _state: mock,
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("initialize", {}, request_id=1))
    await writer.wait_for(lambda m: m.get("id") == 1)
    await reader.feed(_request("session/new", {"cwd": "."}, request_id=2))
    reply = await writer.wait_for(lambda m: m.get("id") == 2)
    sid = reply["result"]["sessionId"]

    await reader.feed(
        _request(
            "session/message",
            {"sessionId": sid, "message": "long task"},
            request_id=3,
        )
    )
    # Wait for turn_start to confirm the turn has actually entered the loop.
    await writer.wait_for(
        lambda m: m.get("method") == "session/update"
        and m.get("params", {}).get("update", {}).get("sessionUpdate") == "turn_start"
    )

    await reader.feed(
        _request("session/cancel", {"sessionId": sid}, request_id=4)
    )
    cancel_reply = await writer.wait_for(lambda m: m.get("id") == 4)
    assert cancel_reply["result"]["cancelled"] is True

    final = await writer.wait_for(lambda m: m.get("id") == 3, timeout=2.0)
    assert final["result"]["stopReason"] == "cancelled"

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# tool/approve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_approve_resolves_pending_permission() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("initialize", {}, request_id=1))
    await writer.wait_for(lambda m: m.get("id") == 1)
    await reader.feed(_request("session/new", {"cwd": "."}, request_id=2))
    reply = await writer.wait_for(lambda m: m.get("id") == 2)
    sid = reply["result"]["sessionId"]
    state = server.sessions[sid]

    # Drive the server-side bridge directly: emit a permission request,
    # then have the client (us) approve it via tool/approve.
    approval_task = asyncio.create_task(
        server.request_tool_approval(
            state, tool_name="bash", tool_input={"cmd": "ls"},
        )
    )

    perm_msg = await writer.wait_for(
        lambda m: m.get("method") == "session/update"
        and m.get("params", {}).get("update", {}).get("sessionUpdate")
        == "permission_request"
    )
    pid = perm_msg["params"]["update"]["permissionId"]
    assert pid in state.pending_permissions

    await reader.feed(
        _request(
            "tool/approve",
            {"sessionId": sid, "permissionId": pid, "decision": "approve"},
            request_id=9,
        )
    )
    reply = await writer.wait_for(lambda m: m.get("id") == 9)
    assert reply["result"]["applied"] is True

    decision = await asyncio.wait_for(approval_task, timeout=1.0)
    assert decision is True
    assert pid not in state.pending_permissions

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_tool_approve_deny_returns_false() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("initialize", {}, request_id=1))
    await writer.wait_for(lambda m: m.get("id") == 1)
    await reader.feed(_request("session/new", {"cwd": "."}, request_id=2))
    reply = await writer.wait_for(lambda m: m.get("id") == 2)
    sid = reply["result"]["sessionId"]
    state = server.sessions[sid]

    approval_task = asyncio.create_task(
        server.request_tool_approval(
            state, tool_name="bash", tool_input={"cmd": "rm -rf /"},
        )
    )
    perm_msg = await writer.wait_for(
        lambda m: m.get("method") == "session/update"
        and m.get("params", {}).get("update", {}).get("sessionUpdate")
        == "permission_request"
    )
    pid = perm_msg["params"]["update"]["permissionId"]

    await reader.feed(
        _request(
            "tool/approve",
            {"sessionId": sid, "permissionId": pid, "decision": "deny"},
            request_id=4,
        )
    )
    await writer.wait_for(lambda m: m.get("id") == 4)

    decision = await asyncio.wait_for(approval_task, timeout=1.0)
    assert decision is False

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# Misc — unknown method, parse error, notifications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_method_returns_method_not_found() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("initialize", {}, request_id=1))
    await writer.wait_for(lambda m: m.get("id") == 1)

    await reader.feed(_request("nope/does-not-exist", {}, request_id=42))
    reply = await writer.wait_for(lambda m: m.get("id") == 42)
    assert "error" in reply
    assert reply["error"]["code"] == -32601

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_parse_error_emits_parse_error_reply() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed("not json {{{")
    reply = await writer.wait_for(
        lambda m: "error" in m and m["error"].get("code") == -32700
    )
    assert reply["id"] is None

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_notification_without_id_does_not_reply() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("initialize", {}, request_id=1))
    await writer.wait_for(lambda m: m.get("id") == 1)
    msgs_before = len(writer.messages())

    # ``id``-less message: a response, not a request — server must ignore.
    await reader.feed(_notify("client/heartbeat", {"ts": 0}))
    await asyncio.sleep(0.05)
    assert len(writer.messages()) == msgs_before

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# Direct dataclass smoke tests
# ---------------------------------------------------------------------------


def test_session_state_defaults() -> None:
    s = ACPSessionState(session_id="abc", working_dir="/x")
    assert s.session_id == "abc"
    assert s.working_dir == "/x"
    assert s.agent is None
    assert s.active_turn is False
    assert s.pending_permissions == {}

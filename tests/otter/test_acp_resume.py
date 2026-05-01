"""Tests for ACP ``session/resume`` — reconnect-and-replay (W7 parity).

The HTTP server (:mod:`chimera.otter.server`) honors the SSE
``Last-Event-ID`` header so a reconnecting client picks up exactly
where it left off. ACP runs over stdio (no HTTP headers), so the
equivalent is a method call: ``session/resume`` with a
``sinceEventId`` cursor. These tests exercise that surface end-to-end
with synthetic notifications and verify:

* Each ``session/update`` notification is stamped with a monotonic
  per-session ``eventId``.
* ``session/resume`` replays only the notifications the client missed.
* Replays come back as plain ``session/update`` frames so the client's
  live-stream handler can process them without a second code path.
* Cursors equal-to or above the latest id replay nothing.
* A cursor below the oldest retained id flips ``truncated=True``.
* ``session/resume`` reports the correct ``replayed`` count and
  current ``lastEventId``.
* ``initialize`` advertises the new resume capability.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from chimera.otter.acp import (
    OTTER_ACP_PROTOCOL_VERSION,
    ACPSessionState,
    OtterACPServer,
)


# ---------------------------------------------------------------------------
# Fakes (mirror the shapes used in tests/otter/test_acp.py).
# ---------------------------------------------------------------------------


class _FakeReader:
    """In-memory ``_LineReader`` fed by ``feed`` / ``close``."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def feed(self, line: str) -> None:
        await self._queue.put(line.encode("utf-8") + b"\n")

    async def close(self) -> None:
        await self._queue.put(b"")

    async def readline(self) -> bytes:
        return await self._queue.get()


class _FakeWriter:
    """In-memory ``_LineWriter`` capturing every JSON object written."""

    def __init__(self) -> None:
        self.lines: list[bytes] = []
        self._event = asyncio.Event()

    async def write(self, data: bytes) -> None:
        for chunk in data.splitlines():
            if chunk.strip():
                self.lines.append(chunk)
        self._event.set()

    def messages(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.lines]

    async def wait_for(
        self,
        predicate: Any,
        timeout: float = 1.0,
    ) -> dict[str, Any]:
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
    """Minimal mock — these tests don't run a real turn."""

    async def async_run(self, task: str, env: Any | None) -> Any:
        class _R:
            output = ""
            success = True
            error = None
            steps = 1

        return _R()


def _request(method: str, params: Any, *, request_id: int = 1) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    )


async def _drive_server(
    server: OtterACPServer, reader: _FakeReader
) -> asyncio.Task[None]:
    task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0)
    return task


async def _new_session(
    reader: _FakeReader, writer: _FakeWriter, *, sid_only: bool = True
) -> str:
    """Drive ``initialize`` + ``session/new`` and return the new session id."""
    await reader.feed(_request("initialize", {}, request_id=1))
    await writer.wait_for(lambda m: m.get("id") == 1)
    await reader.feed(_request("session/new", {"cwd": "."}, request_id=2))
    reply = await writer.wait_for(lambda m: m.get("id") == 2)
    sid = reply["result"]["sessionId"]
    assert isinstance(sid, str)
    return sid


# ---------------------------------------------------------------------------
# Capability advertisement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_advertises_resume_capability() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)

    await reader.feed(_request("initialize", {}, request_id=1))
    reply = await writer.wait_for(lambda m: m.get("id") == 1)
    caps = reply["result"]["agentCapabilities"]
    assert caps["sessionCapabilities"]["cancel"] is True
    assert caps["sessionCapabilities"]["resume"] is True
    assert caps["eventIds"] is True
    assert reply["result"]["protocolVersion"] == OTTER_ACP_PROTOCOL_VERSION

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# Direct emit/resume — synthetic notifications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notifications_carry_monotonic_event_id() -> None:
    """Each ``session/update`` is tagged with a fresh monotonic id."""
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)
    sid = await _new_session(reader, writer)
    state = server.sessions[sid]

    for i in range(5):
        await server._notify(
            "session/update",
            {"sessionId": sid, "update": {"sessionUpdate": "tick", "n": i}},
        )

    notifs = [
        m for m in writer.messages()
        if m.get("method") == "session/update"
        and m.get("params", {}).get("sessionId") == sid
    ]
    assert len(notifs) == 5
    ids = [n["params"]["eventId"] for n in notifs]
    assert ids == [1, 2, 3, 4, 5]
    assert state.last_event_id == 5
    assert len(state.event_history) == 5

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_resume_replays_only_missed_events() -> None:
    """Reconnect after seeing 2 events with ``sinceEventId=2`` → only 3,4,5."""
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)
    sid = await _new_session(reader, writer)

    # Synthetic agent emits 5 notifications.
    for i in range(1, 6):
        await server._notify(
            "session/update",
            {
                "sessionId": sid,
                "update": {"sessionUpdate": "chunk", "n": i},
            },
        )

    # Snapshot the messages the "first connection" already processed —
    # the client got events 1 and 2 before its connection dropped.
    before_resume = len(writer.messages())

    # Reconnect: client passes the highest id it saw.
    await reader.feed(
        _request(
            "session/resume",
            {"sessionId": sid, "sinceEventId": 2},
            request_id=99,
        )
    )
    reply = await writer.wait_for(lambda m: m.get("id") == 99)
    assert reply["result"]["replayed"] == 3
    assert reply["result"]["lastEventId"] == 5
    assert reply["result"]["truncated"] is False

    # The frames written *between* the resume call and the reply are the
    # replayed notifications — and they're plain ``session/update`` envelopes.
    new_messages = writer.messages()[before_resume:]
    replayed = [
        m for m in new_messages
        if m.get("method") == "session/update"
        and m.get("params", {}).get("sessionId") == sid
    ]
    assert [m["params"]["eventId"] for m in replayed] == [3, 4, 5]
    assert [m["params"]["update"]["n"] for m in replayed] == [3, 4, 5]
    # Critically: events 1 and 2 are NOT replayed.
    assert all(m["params"]["eventId"] not in {1, 2} for m in replayed)

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_resume_with_cursor_at_latest_replays_nothing() -> None:
    """A client that's already current sees zero replay frames."""
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)
    sid = await _new_session(reader, writer)

    for i in range(3):
        await server._notify(
            "session/update",
            {"sessionId": sid, "update": {"sessionUpdate": "tick", "n": i}},
        )

    before = len(writer.messages())
    await reader.feed(
        _request(
            "session/resume",
            {"sessionId": sid, "sinceEventId": 3},
            request_id=10,
        )
    )
    reply = await writer.wait_for(lambda m: m.get("id") == 10)
    assert reply["result"]["replayed"] == 0
    assert reply["result"]["lastEventId"] == 3

    # Only the resume reply itself was added to the wire, no replay frames.
    new = writer.messages()[before:]
    replayed = [m for m in new if m.get("method") == "session/update"]
    assert replayed == []

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_resume_cursor_zero_replays_everything() -> None:
    """``sinceEventId=0`` is the canonical "replay everything" cursor."""
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)
    sid = await _new_session(reader, writer)

    for i in range(4):
        await server._notify(
            "session/update",
            {"sessionId": sid, "update": {"sessionUpdate": "tick", "n": i}},
        )

    before = len(writer.messages())
    await reader.feed(
        _request(
            "session/resume",
            {"sessionId": sid, "sinceEventId": 0},
            request_id=11,
        )
    )
    reply = await writer.wait_for(lambda m: m.get("id") == 11)
    assert reply["result"]["replayed"] == 4

    new = writer.messages()[before:]
    replayed = [
        m for m in new
        if m.get("method") == "session/update"
        and m.get("params", {}).get("sessionId") == sid
    ]
    assert [m["params"]["eventId"] for m in replayed] == [1, 2, 3, 4]

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_resume_cursor_below_oldest_marks_truncated() -> None:
    """When the buffer evicted history, the resume reply flags it."""
    reader = _FakeReader()
    writer = _FakeWriter()
    # Tiny history limit forces FIFO eviction after a few events.
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
        history_limit=3,
    )
    task = await _drive_server(server, reader)
    sid = await _new_session(reader, writer)
    state = server.sessions[sid]
    assert state.history_limit == 3

    # Emit 5 events; the buffer only retains the last 3 (ids 3,4,5).
    for i in range(5):
        await server._notify(
            "session/update",
            {"sessionId": sid, "update": {"sessionUpdate": "tick", "n": i}},
        )
    assert [e["eventId"] for e in state.event_history] == [3, 4, 5]

    # Client thinks it last saw id=1 — but events 2 was evicted, so
    # we can't fully cover the gap.
    await reader.feed(
        _request(
            "session/resume",
            {"sessionId": sid, "sinceEventId": 1},
            request_id=12,
        )
    )
    reply = await writer.wait_for(lambda m: m.get("id") == 12)
    assert reply["result"]["truncated"] is True
    # We still replay everything we still have (ids 3,4,5).
    assert reply["result"]["replayed"] == 3
    assert reply["result"]["lastEventId"] == 5

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_resume_unknown_session_errors() -> None:
    """``session/resume`` rejects sessions the server doesn't know."""
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
            "session/resume",
            {"sessionId": "no-such-session", "sinceEventId": 0},
            request_id=2,
        )
    )
    reply = await writer.wait_for(lambda m: m.get("id") == 2)
    assert "error" in reply
    assert reply["error"]["code"] == -32602

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_resume_malformed_cursor_clamps_to_zero() -> None:
    """A non-integer ``sinceEventId`` is treated as ``0`` (replay all)."""
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)
    sid = await _new_session(reader, writer)

    for i in range(2):
        await server._notify(
            "session/update",
            {"sessionId": sid, "update": {"sessionUpdate": "tick", "n": i}},
        )

    await reader.feed(
        _request(
            "session/resume",
            {"sessionId": sid, "sinceEventId": "not-an-int"},
            request_id=21,
        )
    )
    reply = await writer.wait_for(lambda m: m.get("id") == 21)
    assert reply["result"]["replayed"] == 2

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_resume_after_disconnect_full_round_trip() -> None:
    """Plot from the brief: emit 5 → client drops at 2 → resume from 2."""
    reader = _FakeReader()
    writer = _FakeWriter()
    server = OtterACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive_server(server, reader)
    sid = await _new_session(reader, writer)

    # Synthetic "agent" emits 5 notifications (the brief's scenario).
    payloads = [
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "a"}},
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "b"}},
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "c"}},
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "d"}},
        {"sessionUpdate": "turn_end", "stopReason": "end_turn"},
    ]
    for upd in payloads:
        await server._notify(
            "session/update",
            {"sessionId": sid, "update": upd},
        )

    # The client "sees" events 1 and 2 then disconnects. We model the
    # disconnect by snapshotting the writer's buffer position; everything
    # newer is what the reconnecting client must receive.
    seen_through_id = 2
    pre_resume = len(writer.messages())

    # Reconnect with the cursor.
    await reader.feed(
        _request(
            "session/resume",
            {"sessionId": sid, "sinceEventId": seen_through_id},
            request_id=77,
        )
    )
    reply = await writer.wait_for(lambda m: m.get("id") == 77)
    assert reply["result"]["replayed"] == 3
    assert reply["result"]["truncated"] is False

    # Verify exactly events 3, 4, 5 came back as plain session/update frames
    # — and no events 1 or 2 leaked into the replay slice.
    replayed = [
        m for m in writer.messages()[pre_resume:]
        if m.get("method") == "session/update"
        and m.get("params", {}).get("sessionId") == sid
    ]
    assert len(replayed) == 3
    assert [m["params"]["eventId"] for m in replayed] == [3, 4, 5]
    assert replayed[0]["params"]["update"]["content"]["text"] == "c"
    assert replayed[1]["params"]["update"]["content"]["text"] == "d"
    assert replayed[2]["params"]["update"]["sessionUpdate"] == "turn_end"

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# Direct dataclass smoke tests for the new fields
# ---------------------------------------------------------------------------


def test_session_state_event_history_defaults_empty() -> None:
    """Fresh sessions start with an empty history and zero counter."""
    s = ACPSessionState(session_id="abc", working_dir="/x")
    assert s.last_event_id == 0
    assert s.event_history == []
    assert s.history_limit > 0

"""Tests for :mod:`chimera.ferret.ide` — the IDE-first ferret ACP server.

These mirror :mod:`tests.otter.test_acp` in shape: in-memory reader/writer
fakes, a mock agent, JSON-RPC frames typed in by hand. We additionally
verify the four IDE-friendly notification kinds —

* ``code/diff``
* ``editor/open_file``
* ``terminal/output``
* ``progress/step``

— emit on the right helper calls, that ``ide_schema=False`` degrades
each one to a plain otter shape, and that ``initialize`` advertises the
expanded capability bag. The ``unified_diff`` helper and the
``maybe_serve_ide_acp`` late-binding hook get their own direct unit
tests so a future FF1 wiring landing doesn't need this test file to be
re-run end-to-end.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import pytest

from chimera.ferret.ide import (
    FERRET_ACP_AGENT_NAME,
    FERRET_ACP_PROTOCOL_VERSION,
    FerretACPServer,
    build_ide_serve_parser,
    maybe_serve_ide_acp,
    unified_diff,
)
from chimera.otter.acp import ACPSessionState


# ---------------------------------------------------------------------------
# Fakes (mirrors tests/otter/test_acp.py)
# ---------------------------------------------------------------------------


class _FakeReader:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def feed(self, line: str) -> None:
        await self._queue.put(line.encode("utf-8") + b"\n")

    async def close(self) -> None:
        await self._queue.put(b"")

    async def readline(self) -> bytes:
        return await self._queue.get()


class _FakeWriter:
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
    def __init__(self, *, output: str = "ok") -> None:
        self.output = output
        self.calls: list[str] = []

    async def async_run(self, task: str, env: Any | None) -> Any:
        self.calls.append(task)

        class _R:
            pass

        r = _R()
        r.output = self.output  # type: ignore[attr-defined]
        r.success = True  # type: ignore[attr-defined]
        r.error = None  # type: ignore[attr-defined]
        return r


def _request(method: str, params: Any, *, request_id: int = 1) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    )


async def _drive(server: FerretACPServer) -> asyncio.Task[None]:
    task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0)
    return task


async def _init_and_open_session(
    server: FerretACPServer,
    reader: _FakeReader,
    writer: _FakeWriter,
) -> str:
    await reader.feed(_request("initialize", {}, request_id=1))
    await writer.wait_for(lambda m: m.get("id") == 1)
    await reader.feed(_request("session/new", {"cwd": "."}, request_id=2))
    reply = await writer.wait_for(lambda m: m.get("id") == 2)
    sid: str = reply["result"]["sessionId"]
    return sid


# ---------------------------------------------------------------------------
# initialize advertises the IDE capability bag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_advertises_ide_capabilities() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive(server)

    await reader.feed(_request("initialize", {"protocolVersion": 1}))
    reply = await writer.wait_for(lambda m: m.get("id") == 1)

    result = reply["result"]
    assert result["protocolVersion"] == FERRET_ACP_PROTOCOL_VERSION
    assert result["agentInfo"]["name"] == FERRET_ACP_AGENT_NAME
    caps = result["agentCapabilities"]
    assert caps["ideSchema"] is True
    assert caps["ideNotifications"]["codeDiff"] is True
    assert caps["ideNotifications"]["editorOpenFile"] is True
    assert caps["ideNotifications"]["terminalOutput"] is True
    assert caps["ideNotifications"]["progressStep"] is True
    # Otter capabilities are still present (composition over rebuild).
    assert caps["promptCapabilities"]["text"] is True
    assert caps["sessionCapabilities"]["cancel"] is True

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_initialize_with_ide_schema_off_hides_kinds() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
        ide_schema=False,
    )
    task = await _drive(server)

    await reader.feed(_request("initialize", {}, request_id=1))
    reply = await writer.wait_for(lambda m: m.get("id") == 1)

    caps = reply["result"]["agentCapabilities"]
    assert caps["ideSchema"] is False
    # The expanded notification list is not advertised when schema is off.
    assert "ideNotifications" not in caps

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# code/diff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_code_diff_emits_unified_diff() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive(server)
    sid = await _init_and_open_session(server, reader, writer)
    state = server.sessions[sid]

    await server.notify_code_diff(
        state,
        path="src/foo.py",
        before="hello\nworld\n",
        after="hello\nWORLD\n",
    )

    msg = await writer.wait_for(
        lambda m: (
            m.get("method") == "session/update"
            and m.get("params", {}).get("update", {}).get("sessionUpdate")
            == "code/diff"
        )
    )
    update = msg["params"]["update"]
    assert update["path"] == "src/foo.py"
    assert update["changeKind"] == "update"
    assert "--- src/foo.py" in update["unifiedDiff"]
    assert "+++ src/foo.py" in update["unifiedDiff"]
    assert "-world" in update["unifiedDiff"]
    assert "+WORLD" in update["unifiedDiff"]

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_notify_code_diff_infers_add_kind_for_new_file() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive(server)
    sid = await _init_and_open_session(server, reader, writer)
    state = server.sessions[sid]

    await server.notify_code_diff(
        state, path="new.py", before="", after="print('hi')\n"
    )
    msg = await writer.wait_for(
        lambda m: m.get("params", {}).get("update", {}).get("sessionUpdate")
        == "code/diff"
    )
    assert msg["params"]["update"]["changeKind"] == "add"

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_notify_code_diff_falls_back_when_schema_off() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
        ide_schema=False,
    )
    task = await _drive(server)
    sid = await _init_and_open_session(server, reader, writer)
    state = server.sessions[sid]

    await server.notify_code_diff(
        state, path="src/foo.py", before="a\n", after="b\n"
    )

    msg = await writer.wait_for(
        lambda m: m.get("method") == "session/update"
        and m.get("params", {}).get("update", {}).get("sessionUpdate")
        == "tool_call_finished"
    )
    assert msg["params"]["update"]["tool"]["input"]["path"] == "src/foo.py"
    # Verify the rich kind was NOT emitted.
    kinds = [
        m["params"]["update"]["sessionUpdate"]
        for m in writer.messages()
        if m.get("method") == "session/update"
    ]
    assert "code/diff" not in kinds

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# editor/open_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_editor_open_file_emits_full_payload() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive(server)
    sid = await _init_and_open_session(server, reader, writer)
    state = server.sessions[sid]

    await server.notify_editor_open_file(
        state,
        path="src/test_x.py",
        line=42,
        column=5,
        preview="def test_x(): ...",
    )

    msg = await writer.wait_for(
        lambda m: m.get("params", {}).get("update", {}).get("sessionUpdate")
        == "editor/open_file"
    )
    update = msg["params"]["update"]
    assert update["path"] == "src/test_x.py"
    assert update["line"] == 42
    assert update["column"] == 5
    assert update["preview"] == "def test_x(): ..."

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_notify_editor_open_file_falls_back_to_text() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
        ide_schema=False,
    )
    task = await _drive(server)
    sid = await _init_and_open_session(server, reader, writer)
    state = server.sessions[sid]

    await server.notify_editor_open_file(state, path="x.py", line=10)
    msg = await writer.wait_for(
        lambda m: m.get("params", {}).get("update", {}).get("sessionUpdate")
        == "agent_message_chunk"
    )
    text = msg["params"]["update"]["content"]["text"]
    assert "x.py" in text
    assert ":10" in text

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# terminal/output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_terminal_output_emits_chunk() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive(server)
    sid = await _init_and_open_session(server, reader, writer)
    state = server.sessions[sid]

    await server.notify_terminal_output(
        state,
        process_id="proc-1",
        stream="stdout",
        chunk="hello\n",
        sequence=3,
        cap_reached=False,
    )

    msg = await writer.wait_for(
        lambda m: m.get("params", {}).get("update", {}).get("sessionUpdate")
        == "terminal/output"
    )
    update = msg["params"]["update"]
    assert update["processId"] == "proc-1"
    assert update["stream"] == "stdout"
    assert update["chunk"] == "hello\n"
    assert update["sequence"] == 3
    assert update["capReached"] is False

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_notify_terminal_output_rejects_bad_stream() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive(server)
    sid = await _init_and_open_session(server, reader, writer)
    state = server.sessions[sid]

    with pytest.raises(ValueError):
        await server.notify_terminal_output(
            state, process_id="p", stream="weird", chunk="x"
        )

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# progress/step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_progress_step_emits_marker() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
    )
    task = await _drive(server)
    sid = await _init_and_open_session(server, reader, writer)
    state = server.sessions[sid]

    await server.notify_progress_step(
        state, phase="thinking", step=2, detail="planning"
    )

    msg = await writer.wait_for(
        lambda m: m.get("params", {}).get("update", {}).get("sessionUpdate")
        == "progress/step"
    )
    update = msg["params"]["update"]
    assert update["phase"] == "thinking"
    assert update["step"] == 2
    assert update["detail"] == "planning"

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_notify_progress_step_silent_when_schema_off() -> None:
    reader = _FakeReader()
    writer = _FakeWriter()
    server = FerretACPServer(
        agent_factory=lambda _state: _MockAgent(),
        reader=reader,
        writer=writer,
        ide_schema=False,
    )
    task = await _drive(server)
    sid = await _init_and_open_session(server, reader, writer)
    state = server.sessions[sid]

    msgs_before = len(writer.messages())
    await server.notify_progress_step(state, phase="thinking", step=1)
    # Yield so any (incorrect) emission would have landed.
    await asyncio.sleep(0.05)
    msgs_after = len(writer.messages())
    assert msgs_after == msgs_before

    await reader.close()
    server.stop()
    await asyncio.wait_for(task, timeout=1.0)


# ---------------------------------------------------------------------------
# Direct unit tests — unified_diff, parser, late-binding hook
# ---------------------------------------------------------------------------


def test_unified_diff_returns_empty_when_unchanged() -> None:
    assert unified_diff(path="x.py", before="a\n", after="a\n") == ""


def test_unified_diff_has_expected_headers() -> None:
    out = unified_diff(path="x.py", before="a\n", after="b\n")
    assert "--- x.py" in out
    assert "+++ x.py" in out
    assert "-a" in out
    assert "+b" in out


def test_build_ide_serve_parser_registers_flags() -> None:
    p = argparse.ArgumentParser()
    build_ide_serve_parser(p)
    args = p.parse_args(["--http", "--ide-schema", "false"])
    assert args.http is True
    assert args.ide_schema is False

    args2 = p.parse_args([])
    assert args2.http is False
    assert args2.ide_schema is True

    args3 = p.parse_args(["--ide-schema", "true"])
    assert args3.ide_schema is True

    with pytest.raises(SystemExit):
        p.parse_args(["--ide-schema", "definitely-not-a-bool"])


def test_maybe_serve_ide_acp_returns_none_when_http() -> None:
    args = argparse.Namespace(http=True, ide_schema=True)
    rc = maybe_serve_ide_acp(args)
    assert rc is None


def test_maybe_serve_ide_acp_runs_acp_when_not_http(monkeypatch: Any) -> None:
    """When ``--http`` is absent, ferret should boot the IDE ACP server.

    We don't actually want to spin up an asyncio loop on stdio in a unit
    test, so we monkey-patch ``serve_stdio_ide`` to record the call and
    return a sentinel exit code.
    """
    captured: dict[str, Any] = {}

    def _fake_serve(factory: Any, *, ide_schema: bool = True) -> int:
        captured["factory"] = factory
        captured["ide_schema"] = ide_schema
        return 7

    monkeypatch.setattr("chimera.ferret.ide.serve_stdio_ide", _fake_serve)

    args = argparse.Namespace(http=False, ide_schema=False, model=None)
    rc = maybe_serve_ide_acp(args)
    assert rc == 7
    assert captured["ide_schema"] is False
    assert callable(captured["factory"])


# ---------------------------------------------------------------------------
# Direct dataclass smoke
# ---------------------------------------------------------------------------


def test_session_state_imports_from_otter() -> None:
    """Sanity: ferret reuses otter's session-state dataclass."""
    s = ACPSessionState(session_id="ferret-x", working_dir="/tmp/y")
    assert s.session_id == "ferret-x"
    assert s.working_dir == "/tmp/y"

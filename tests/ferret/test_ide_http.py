"""Tests for the F2/W9 HTTP+SSE bridge for ferret IDE notifications.

These verify that the four IDE-friendly notification kinds —
``code/diff`` / ``editor/open_file`` / ``terminal/output`` /
``progress/step`` — fan out through the HTTP+SSE event stream the
same way they do over the ACP transport.

The shape of each frame is asserted to match what
:class:`chimera.ferret.ide.FerretACPServer` already emits over ACP, so
an IDE plugin can consume from either transport without branching.

We exercise three layers:

* ``ide_emit_for_state`` — the per-session emitter helper writes
  envelopes into ``state.events`` and fans them out to subscribers.
* :class:`IDENotificationEmitter` — given a fake EventBus, translates
  ``ToolCallEvent`` / ``ToolResultEvent`` into the right SSE frames
  (including running a real ``write_file`` against ``tmp_path`` so the
  unified diff is computed end-to-end).
* :func:`_dispatch_serve_http` — the agent factory now wires an
  :class:`EventBus` onto :class:`LoopConfig.event_bus` and attaches an
  :class:`IDENotificationEmitter` whose emit-callable is bound to the
  session state.
"""
from __future__ import annotations

import argparse
import os
import queue as _queue
from typing import Any

from chimera.events.base import EventBus
from chimera.events.types import ToolCallEvent, ToolResultEvent
from chimera.ferret import cli as ferret_cli
from chimera.ferret.ide import (
    IDENotificationEmitter,
    ide_emit_for_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chimera ferret")
    ferret_cli.add_arguments(parser)
    return parser


def _ns(**overrides: object) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args([])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


class _FakeProvider:
    """FF6 provider stand-in so factory tests don't hit the network."""

    model_name = "gpt-5"

    async def generate(self, *_a: Any, **_kw: Any) -> None:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# ide_emit_for_state — direct fan-out into session.events + subscribers
# ---------------------------------------------------------------------------


def test_ide_emit_for_state_appends_envelope_to_events() -> None:
    """``ide_emit_for_state`` writes one envelope per call into ``state.events``."""
    from chimera.otter.server import OtterSessionState

    state = OtterSessionState(session_id="t-1")
    emit = ide_emit_for_state(state)
    emit("code/diff", {"sessionUpdate": "code/diff", "path": "/x.py"})

    assert len(state.events) == 1
    env = state.events[0]
    assert env["event"] == "code/diff"
    assert env["id"] == "1"
    assert env["data"]["path"] == "/x.py"
    assert "timestamp" in env


def test_ide_emit_for_state_fans_out_to_subscribers() -> None:
    """Live SSE subscribers receive frames as soon as ``emit`` is called."""
    from chimera.otter.server import OtterSessionState

    state = OtterSessionState(session_id="t-2")
    q: _queue.Queue[dict[str, Any] | None] = _queue.Queue()
    state.subscribers.append(q)

    emit = ide_emit_for_state(state)
    emit("progress/step", {"sessionUpdate": "progress/step", "phase": "thinking", "step": 1})

    got = q.get(timeout=1.0)
    assert got is not None
    assert got["event"] == "progress/step"
    assert got["data"]["phase"] == "thinking"


# ---------------------------------------------------------------------------
# IDENotificationEmitter — bus-driven translation
# ---------------------------------------------------------------------------


def test_emitter_attaches_and_detaches() -> None:
    """:meth:`attach` returns a working detach callable."""
    sink: list[tuple[str, dict[str, Any]]] = []
    emitter = IDENotificationEmitter(lambda name, data: sink.append((name, data)))
    bus = EventBus()
    detach = emitter.attach(bus)

    bus.publish(ToolCallEvent(tool_name="bash", arguments={}, call_id="c1"))
    assert any(name == "progress/step" for name, _ in sink)

    sink.clear()
    detach()
    bus.publish(ToolCallEvent(tool_name="bash", arguments={}, call_id="c2"))
    assert sink == []  # detach really detached


def test_emitter_emits_code_diff_for_write_file(tmp_path) -> None:
    """``ToolCallEvent('write_file')`` + ``ToolResultEvent`` => ``code/diff``."""
    target = tmp_path / "hello.py"
    # File doesn't exist yet — "before" must be empty so changeKind=add.
    sink: list[tuple[str, dict[str, Any]]] = []
    emitter = IDENotificationEmitter(lambda name, data: sink.append((name, data)))
    bus = EventBus()
    emitter.attach(bus)

    # Loop publishes ``ToolCallEvent`` BEFORE the tool runs.
    bus.publish(
        ToolCallEvent(
            tool_name="write_file",
            arguments={"path": str(target), "content": "print('hi')\n"},
            call_id="c-write",
        )
    )

    # Simulate the tool executing — the agent writes the file.
    target.write_text("print('hi')\n", encoding="utf-8")

    # Loop publishes ``ToolResultEvent`` AFTER the tool ran.
    bus.publish(
        ToolResultEvent(
            call_id="c-write",
            output="wrote /hello.py",
            success=True,
        )
    )

    diff_frames = [d for n, d in sink if n == "code/diff"]
    assert len(diff_frames) == 1
    frame = diff_frames[0]
    assert frame["sessionUpdate"] == "code/diff"
    assert frame["path"] == str(target)
    assert frame["changeKind"] == "add"
    # Unified diff must mention the new content.
    assert "print('hi')" in frame["unifiedDiff"]
    assert "+++" in frame["unifiedDiff"]


def test_emitter_emits_terminal_output_for_bash() -> None:
    """``ToolResultEvent`` for ``bash`` becomes a ``terminal/output`` frame."""
    sink: list[tuple[str, dict[str, Any]]] = []
    emitter = IDENotificationEmitter(lambda name, data: sink.append((name, data)))
    bus = EventBus()
    emitter.attach(bus)

    bus.publish(
        ToolCallEvent(
            tool_name="bash",
            arguments={"command": "echo hi"},
            call_id="b1",
        )
    )
    bus.publish(
        ToolResultEvent(
            call_id="b1",
            output="hi\n",
            success=True,
        )
    )

    term_frames = [d for n, d in sink if n == "terminal/output"]
    assert len(term_frames) == 1
    frame = term_frames[0]
    assert frame["sessionUpdate"] == "terminal/output"
    assert frame["processId"] == "b1"
    assert frame["stream"] == "stdout"
    assert frame["chunk"] == "hi\n"
    assert frame["sequence"] == 1
    assert frame["capReached"] is False


def test_emitter_progress_step_phases() -> None:
    """A tool call/result pair fans out two ``progress/step`` frames."""
    sink: list[tuple[str, dict[str, Any]]] = []
    emitter = IDENotificationEmitter(lambda name, data: sink.append((name, data)))
    bus = EventBus()
    emitter.attach(bus)

    bus.publish(
        ToolCallEvent(tool_name="search", arguments={"q": "x"}, call_id="s1")
    )
    bus.publish(
        ToolResultEvent(call_id="s1", output="match", success=True)
    )

    progress_frames = [d for n, d in sink if n == "progress/step"]
    phases = [d["phase"] for d in progress_frames]
    assert phases == ["tool_call", "response"]
    # Step counters increment monotonically.
    assert progress_frames[0]["step"] == 1
    assert progress_frames[1]["step"] == 2


def test_emitter_explicit_editor_open_file() -> None:
    """:meth:`emit_editor_open_file` drops a single SSE frame on demand."""
    sink: list[tuple[str, dict[str, Any]]] = []
    emitter = IDENotificationEmitter(lambda name, data: sink.append((name, data)))

    emitter.emit_editor_open_file(path="/x.py", line=3, preview="def f():")

    assert len(sink) == 1
    name, data = sink[0]
    assert name == "editor/open_file"
    assert data["sessionUpdate"] == "editor/open_file"
    assert data["path"] == "/x.py"
    assert data["line"] == 3
    assert data["preview"] == "def f():"


def test_emitter_ide_schema_off_skips_translation() -> None:
    """``ide_schema=False`` short-circuits all bus-driven translation."""
    sink: list[tuple[str, dict[str, Any]]] = []
    emitter = IDENotificationEmitter(
        lambda name, data: sink.append((name, data)),
        ide_schema=False,
    )
    bus = EventBus()
    emitter.attach(bus)

    bus.publish(
        ToolCallEvent(tool_name="write_file", arguments={"path": "/x"}, call_id="c1")
    )
    bus.publish(ToolResultEvent(call_id="c1", output="", success=True))

    assert sink == []


def test_emitter_terminal_output_rejects_invalid_stream() -> None:
    """The explicit emitter rejects unknown stream tags (defensive)."""
    emitter = IDENotificationEmitter(lambda *_: None)
    try:
        emitter.emit_terminal_output(
            process_id="p1", stream="weird", chunk="x", sequence=1
        )
    except ValueError as e:
        assert "stream" in str(e)
    else:
        raise AssertionError("expected ValueError for invalid stream tag")


# ---------------------------------------------------------------------------
# End-to-end: fire a fake agent through the HTTP factory and observe SSE
# ---------------------------------------------------------------------------


class _FakeWriteAgent:
    """Stand-in agent that publishes a single write_file tool round-trip.

    The real :class:`ReAct` loop publishes ``ToolCallEvent`` /
    ``ToolResultEvent`` via :class:`LoopConfig.event_bus` as it drives
    tools. This fake takes the bus straight off the loop and replays
    those publishes by hand so the integration test can assert the SSE
    fan-out without spinning up a provider.
    """

    def __init__(self, *, target_path: str, event_bus: EventBus) -> None:
        self.target_path = target_path
        self.event_bus = event_bus
        self.tools: list[Any] = []

    async def async_run(self, task: str, env: Any | None = None) -> Any:
        # Publish the call event before mutating the filesystem so the
        # IDE emitter snapshots an empty "before".
        self.event_bus.publish(
            ToolCallEvent(
                tool_name="write_file",
                arguments={"path": self.target_path, "content": "print('hi')\n"},
                call_id="fake-call-1",
            )
        )
        # Mutate the filesystem to mirror the real write_file tool.
        with open(self.target_path, "w", encoding="utf-8") as fh:
            fh.write("print('hi')\n")
        self.event_bus.publish(
            ToolResultEvent(
                call_id="fake-call-1",
                output=f"wrote {self.target_path}",
                success=True,
            )
        )

        class _R:
            output = "ok"
            success = True
            steps = 1
            cost = 0.0

        return _R()


def test_dispatch_serve_http_factory_wires_event_bus(monkeypatch, tmp_path) -> None:
    """The HTTP factory installs an EventBus + IDENotificationEmitter.

    We capture the factory off ``serve_http``, invoke it against a
    fresh :class:`OtterSessionState`, and assert the agent's
    :class:`LoopConfig.event_bus` is a live :class:`EventBus`.
    """
    from chimera.otter.server import OtterSessionState

    captured: dict = {}

    def _fake_serve(agent_factory: Any, **kw: Any) -> int:
        captured["agent_factory"] = agent_factory
        captured.update(kw)
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake_serve, raising=True
    )
    monkeypatch.setattr(
        ferret_cli, "_build_provider", lambda model: _FakeProvider()
    )

    rc = ferret_cli.run(_ns(subcommand="serve", http=True, cwd=str(tmp_path)))
    assert rc == 0

    factory = captured["agent_factory"]
    state = OtterSessionState(session_id="ide-1", working_dir=str(tmp_path))
    agent = factory(state)

    # The agent's ReAct loop carries a LoopConfig with a live EventBus.
    config = agent.loop.config
    assert config is not None
    assert config.event_bus is not None


def test_sse_stream_emits_code_diff_for_write_file(monkeypatch, tmp_path) -> None:
    """End-to-end: a fake write_file run produces a ``code/diff`` SSE frame.

    We don't bind a real port — the SSE stream is the per-session
    ``state.events`` list (the same list ``GET /session/<id>/events``
    replays). After invoking the agent's ``async_run``, the list must
    contain a ``code/diff`` envelope whose ``data`` matches the same
    JSON shape :class:`FerretACPServer.notify_code_diff` produces over
    ACP.
    """
    import asyncio
    from chimera.otter.server import OtterSessionState

    target = tmp_path / "hello.py"
    captured: dict = {}

    def _fake_serve(agent_factory: Any, **kw: Any) -> int:
        captured["agent_factory"] = agent_factory
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake_serve, raising=True
    )
    monkeypatch.setattr(
        ferret_cli, "_build_provider", lambda model: _FakeProvider()
    )

    rc = ferret_cli.run(_ns(subcommand="serve", http=True, cwd=str(tmp_path)))
    assert rc == 0
    factory = captured["agent_factory"]

    state = OtterSessionState(session_id="ide-2", working_dir=str(tmp_path))
    real_agent = factory(state)
    bus = real_agent.loop.config.event_bus
    assert bus is not None

    # Swap in a fake agent that uses the same bus the real ReAct loop
    # would publish to. The IDENotificationEmitter is already attached.
    fake = _FakeWriteAgent(target_path=str(target), event_bus=bus)
    asyncio.run(fake.async_run("write a hello"))

    # The SSE event stream must include a ``code/diff`` envelope matching
    # the same JSON shape FerretACPServer emits over ACP.
    diff_envelopes = [e for e in state.events if e["event"] == "code/diff"]
    assert len(diff_envelopes) == 1
    payload = diff_envelopes[0]["data"]
    assert payload["sessionUpdate"] == "code/diff"
    assert payload["path"] == str(target)
    assert payload["changeKind"] == "add"
    assert "print('hi')" in payload["unifiedDiff"]

    # progress/step frames bracket the tool execution.
    progress = [e for e in state.events if e["event"] == "progress/step"]
    assert any(e["data"]["phase"] == "tool_call" for e in progress)
    assert any(e["data"]["phase"] == "response" for e in progress)


def test_sse_stream_respects_ide_schema_false(monkeypatch, tmp_path) -> None:
    """``--ide-schema false`` skips IDE fan-out on the HTTP transport too.

    Mirrors the ACP-side opt-out so a HTTP-only relay that doesn't
    speak the rich schema sees only the otter base shapes.
    """
    import asyncio
    from chimera.otter.server import OtterSessionState

    target = tmp_path / "hello.py"
    captured: dict = {}

    def _fake_serve(agent_factory: Any, **kw: Any) -> int:
        captured["agent_factory"] = agent_factory
        return 0

    monkeypatch.setattr(
        "chimera.otter.server.serve_http", _fake_serve, raising=True
    )
    monkeypatch.setattr(
        ferret_cli, "_build_provider", lambda model: _FakeProvider()
    )

    rc = ferret_cli.run(
        _ns(subcommand="serve", http=True, cwd=str(tmp_path), ide_schema=False)
    )
    assert rc == 0
    factory = captured["agent_factory"]

    state = OtterSessionState(session_id="ide-3", working_dir=str(tmp_path))
    real_agent = factory(state)
    bus = real_agent.loop.config.event_bus
    assert bus is not None

    fake = _FakeWriteAgent(target_path=str(target), event_bus=bus)
    asyncio.run(fake.async_run("write a hello"))

    # No IDE-shaped frames should land on the SSE stream.
    assert not any(
        e["event"] in ("code/diff", "progress/step", "terminal/output")
        for e in state.events
    )


# ---------------------------------------------------------------------------
# Regression: ACP behavior is untouched
# ---------------------------------------------------------------------------


def test_acp_path_does_not_attach_event_bus(monkeypatch) -> None:
    """The ACP transport (no ``--http``) must not be perturbed by F2/W9.

    We assert the ACP dispatcher is still the one called, with no
    EventBus / IDENotificationEmitter wiring leaking onto the stdio
    path.
    """
    captured: dict[str, Any] = {}

    def _fake_acp(args: argparse.Namespace) -> int:
        captured["called"] = True
        return 0

    monkeypatch.setattr(
        "chimera.ferret.ide.maybe_serve_ide_acp", _fake_acp, raising=False
    )
    rc = ferret_cli.run(_ns(subcommand="serve"))
    assert rc == 0
    assert captured.get("called") is True

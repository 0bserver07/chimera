"""Provider-level cancellation tests for :mod:`chimera.otter.server`.

W6 closed the cooperative-cancel surface (`POST /session/<id>/cancel`
flips a token; the SSE driver checks it between yields). The follow-up
question — and the gap this module closes — is whether a cancel can
preempt an *in-flight provider HTTP call* before the next yield, since a
real LLM call can sit blocking for tens of seconds.

These tests use a mock "agent" that internally calls a mock provider with
a 30-second sleep. They prove that a cancel fired ~1 s in returns within
~5 s — i.e. the provider's ``cancel_event`` plumbing actually preempts the
work, rather than waiting for the long sleep to drain.

Strictly stdlib-only — no real provider, no real httpx call. We exercise
exactly the bridge the brief asks for:

* :class:`chimera.core.cancellation.CancellationToken.threading_event`,
* :meth:`chimera.otter.server.OtterServer._drive_agent_streaming`'s
  ``cancel_event`` forwarding into ``async_run_events``,
* a provider that respects the event by raising mid-call.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import threading
import time
import urllib.request
from typing import Any

from chimera.otter.server import OtterServer


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeLoopEvent:
    """Stand-in for :class:`chimera.core.loop_events.LoopEvent`."""

    type: str
    data: Any
    turn: int = 0
    timestamp: float = 0.0


class _MockSlowProvider:
    """Mock provider whose ``async_complete`` blocks for *sleep_seconds*.

    Honors a passed ``cancel_event``: instead of a single 30-second sleep
    we poll the event in 50ms slices so the cancel hops out as soon as
    the event fires. This mirrors how the real httpx-using providers
    react — a watcher thread/task closes the underlying client and the
    in-flight request raises mid-call.
    """

    def __init__(self, sleep_seconds: float = 30.0) -> None:
        self.sleep_seconds = sleep_seconds
        self.calls = 0
        self.cancel_observed = False

    async def async_complete(
        self,
        prompt: str,
        *,
        cancel_event: threading.Event | None = None,
    ) -> str:
        self.calls += 1
        deadline = time.time() + self.sleep_seconds
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                self.cancel_observed = True
                # Match the real-provider behaviour: raise rather than
                # return a partial response so the agent loop unwinds.
                raise RuntimeError("provider cancelled mid-call")
            await asyncio.sleep(0.05)
        return f"slow-response: {prompt}"


class _ProviderDrivenAgent:
    """Agent that calls a (mock) provider once per `async_run_events` invocation.

    ``async_run_events`` accepts the new ``cancel_event`` keyword and
    forwards it to the provider. A real :class:`Agent.async_run_events`
    delegates to :class:`AgentLoop`, which already plumbs cancellation
    via :class:`AbortSignal`; this test agent is a minimal stand-in that
    proves the *server -> provider* leg.
    """

    def __init__(self, provider: _MockSlowProvider) -> None:
        self.provider = provider

    async def async_run_events(
        self,
        task: str,
        env: Any | None = None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> Any:
        # First yield: ack the prompt so the test can synchronize on
        # "the run is in flight" before firing the cancel.
        yield _FakeLoopEvent(type="assistant_chunk", data={"text": "starting"})
        try:
            result = await self.provider.async_complete(
                task, cancel_event=cancel_event,
            )
        except RuntimeError as exc:
            yield _FakeLoopEvent(
                type="error",
                data={"text": str(exc), "cancelled": True},
            )
            return
        yield _FakeLoopEvent(type="assistant", data={"text": result})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_url(srv: OtterServer) -> str:
    return f"http://127.0.0.1:{srv.port}"


def _post_json(url: str, body: dict[str, Any] | None) -> dict[str, Any]:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(req, timeout=5.0)
    raw = resp.read()
    return json.loads(raw) if raw else {}


def _post_no_body(url: str) -> int:
    req = urllib.request.Request(url, data=b"", method="POST")
    resp = urllib.request.urlopen(req, timeout=5.0)
    return resp.status


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_threading_event_accessor_returns_underlying_event() -> None:
    """``CancellationToken.threading_event()`` exposes the same Event ``cancel`` sets."""
    from chimera.core.cancellation import CancellationToken

    tok = CancellationToken()
    ev = tok.threading_event()
    assert isinstance(ev, threading.Event)
    assert ev.is_set() is False
    tok.cancel()
    # Same object — cancel() sets it.
    assert ev.is_set() is True
    # And calling threading_event() again returns the same instance.
    assert tok.threading_event() is ev


def test_provider_receives_cancel_event_kwarg() -> None:
    """The server forwards ``state.cancel.threading_event()`` to the agent."""
    provider = _MockSlowProvider(sleep_seconds=0.5)
    agent = _ProviderDrivenAgent(provider)
    srv = OtterServer(
        agent_factory=lambda _state: agent,
        host="127.0.0.1",
        port=0,
    )
    srv.start(blocking=False)
    try:
        created = _post_json(f"{_base_url(srv)}/session", body={})
        sid = created["session_id"]
        _post_json(
            f"{_base_url(srv)}/session/{sid}/message",
            body={"text": "hello"},
        )
        # Wait for the slow path to land its terminal result.
        deadline = time.time() + 5.0
        state = srv.get_session(sid)
        assert state is not None
        while time.time() < deadline:
            if any(ev["event"] == "result" for ev in state.events):
                break
            time.sleep(0.02)
        assert provider.calls == 1
        assert provider.cancel_observed is False
    finally:
        srv.shutdown()


def test_cancel_preempts_in_flight_provider_call_within_5s() -> None:
    """A cancel fired ~1s in returns in <5s — proves provider preemption."""
    provider = _MockSlowProvider(sleep_seconds=30.0)
    agent = _ProviderDrivenAgent(provider)
    srv = OtterServer(
        agent_factory=lambda _state: agent,
        host="127.0.0.1",
        port=0,
    )
    srv.start(blocking=False)
    try:
        created = _post_json(f"{_base_url(srv)}/session", body={})
        sid = created["session_id"]

        t0 = time.time()
        _post_json(
            f"{_base_url(srv)}/session/{sid}/message",
            body={"text": "this would take 30s"},
        )

        # Wait until the agent has started emitting the first event so we
        # know the provider call is actually in flight before we cancel.
        state = srv.get_session(sid)
        assert state is not None
        deadline_started = time.time() + 3.0
        while time.time() < deadline_started:
            if any(ev["event"] == "loop_event" for ev in state.events):
                break
            time.sleep(0.02)
        assert any(ev["event"] == "loop_event" for ev in state.events), (
            "agent didn't start emitting; provider mock may be misconfigured"
        )

        # Fire cancel ~1s into the run.
        time.sleep(1.0)
        cancel_status = _post_no_body(f"{_base_url(srv)}/session/{sid}/cancel")
        assert cancel_status == 204

        # Wait for the run to actually wind down (terminal result lands).
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if any(ev["event"] == "result" for ev in state.events):
                break
            time.sleep(0.02)
        elapsed = time.time() - t0

        # Hard SLA: cancel-to-done well under the 30s sleep budget.
        assert elapsed < 5.0, (
            f"cancel didn't preempt the in-flight provider call; "
            f"took {elapsed:.2f}s (sleep budget was 30s)"
        )
        assert provider.cancel_observed is True
        assert state.cancel.is_cancelled is True
        result = next(ev for ev in state.events if ev["event"] == "result")
        assert result["data"]["cancelled"] is True
    finally:
        srv.shutdown()


def test_no_cancel_event_when_factory_doesnt_accept_it() -> None:
    """Old-shape agents (no ``cancel_event`` kwarg) keep working unchanged."""

    class _LegacyAgent:
        def __init__(self) -> None:
            self.invoked = False

        async def async_run_events(
            self, task: str, env: Any | None = None
        ) -> Any:
            self.invoked = True
            yield _FakeLoopEvent(type="assistant", data={"text": f"echo: {task}"})

    agent = _LegacyAgent()
    srv = OtterServer(
        agent_factory=lambda _state: agent,
        host="127.0.0.1",
        port=0,
    )
    srv.start(blocking=False)
    try:
        created = _post_json(f"{_base_url(srv)}/session", body={})
        sid = created["session_id"]
        _post_json(
            f"{_base_url(srv)}/session/{sid}/message",
            body={"text": "hi"},
        )
        # Wait for terminal result.
        state = srv.get_session(sid)
        assert state is not None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if any(ev["event"] == "result" for ev in state.events):
                break
            time.sleep(0.02)
        assert agent.invoked is True
        # And we got a clean (non-cancelled) terminal result — proves the
        # legacy code path still lands without an exception from a bad
        # kwarg forward.
        result = next(ev for ev in state.events if ev["event"] == "result")
        assert result["data"]["cancelled"] is False
    finally:
        srv.shutdown()

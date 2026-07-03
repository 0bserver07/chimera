"""Plumbing tests for ACPRunner (no real subprocess / network / LLM).

The ACP client is injected as a fake via ``client_factory``, so these exercise
the session lifecycle (start/stop, even on error) and result mapping without
spawning an ACP server.
"""

from __future__ import annotations

import pytest

from chimera.acp.types import ACPResponse, ACPSessionConfig, ACPToolCall
from chimera.eval.runners import AgentRunner, AgentRunResult
from chimera.eval.runners.acp import ACPRunner


class _FakeClient:
    """Records start/stop and send calls; returns a canned ACPResponse.

    Args:
        response: The :class:`ACPResponse` to return from ``send_message``.
        start_raises / send_raises / stop_raises: Optional exceptions to raise
            from the corresponding method, to exercise error paths.
    """

    def __init__(
        self,
        response: ACPResponse | None = None,
        start_raises: BaseException | None = None,
        send_raises: BaseException | None = None,
        stop_raises: BaseException | None = None,
    ) -> None:
        self.response = response
        self.start_raises = start_raises
        self.send_raises = send_raises
        self.stop_raises = stop_raises
        self.events: list[str] = []
        self.sent: list[str] = []

    def start(self) -> None:
        self.events.append("start")
        if self.start_raises is not None:
            raise self.start_raises

    def send_message(self, text: str, on_chunk: object = None) -> ACPResponse | None:
        self.sent.append(text)
        if self.send_raises is not None:
            raise self.send_raises
        return self.response

    def stop(self) -> None:
        self.events.append("stop")
        if self.stop_raises is not None:
            raise self.stop_raises


def _response(**overrides: object) -> ACPResponse:
    base: dict[str, object] = {
        "text": "the fix",
        "thoughts": ["t1", "t2"],
        "tool_calls": [
            ACPToolCall(tool_call_id="1", title="edit", tool_kind="edit", status="completed"),
        ],
        "cost": 0.02,
        "input_tokens": 100,
        "output_tokens": 50,
    }
    base.update(overrides)
    return ACPResponse(**base)  # type: ignore[arg-type]


def test_runs_acp_session_and_maps_response() -> None:
    fake = _FakeClient(response=_response())
    runner = ACPRunner(
        "opencode",
        ACPSessionConfig(command=["opencode", "acp"]),
        client_factory=lambda: fake,
    )
    assert isinstance(runner, AgentRunner)  # runtime_checkable

    out = runner.run({"prompt": "fix it"})

    assert isinstance(out, AgentRunResult)
    assert out.status == "completed"
    assert out.answer == "the fix"
    assert out.cost_usd == pytest.approx(0.02)
    assert out.tool_calls == 1
    assert out.raw["input_tokens"] == 100
    assert out.raw["output_tokens"] == 50
    assert out.raw["thoughts"] == 2
    assert fake.sent == ["fix it"]  # prompt extracted from task dict
    assert fake.events == ["start", "stop"]  # full lifecycle
    assert out.wall_clock_sec >= 0.0


def test_send_error_maps_to_error_and_still_stops() -> None:
    fake = _FakeClient(send_raises=RuntimeError("boom"))
    runner = ACPRunner("x", ACPSessionConfig(command=["x"]), client_factory=lambda: fake)

    out = runner.run("a raw prompt")

    assert out.status == "error"
    assert "boom" in out.raw["error"]
    assert fake.events == ["start", "stop"]  # stop called even on error
    assert out.cost_usd == 0.0
    assert out.raw["cost"] == "unknown"


def test_start_error_still_stops() -> None:
    fake = _FakeClient(start_raises=RuntimeError("no start"))
    runner = ACPRunner("x", ACPSessionConfig(command=["x"]), client_factory=lambda: fake)

    out = runner.run("p")

    assert out.status == "error"
    assert fake.events == ["start", "stop"]  # stop still attempted after start failure
    assert fake.sent == []  # send never reached


def test_zero_cost_flagged_unknown() -> None:
    fake = _FakeClient(response=_response(cost=0.0, tool_calls=[], thoughts=[]))
    runner = ACPRunner("x", ACPSessionConfig(command=["x"]), client_factory=lambda: fake)

    out = runner.run("p")

    assert out.status == "completed"
    assert out.cost_usd == 0.0
    assert out.raw["cost"] == "unknown"  # never fabricated
    assert out.tool_calls == 0


def test_default_factory_builds_real_acp_client() -> None:
    from chimera.acp.client import ACPClient

    runner = ACPRunner("x", ACPSessionConfig(command=["true"]))
    client = runner._make_client()  # does NOT start() → no subprocess spawned

    assert isinstance(client, ACPClient)

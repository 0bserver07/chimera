"""Tests for ``chimera.weasel.rpc`` streaming notifications (W13-G10).

When a client calls ``prompt`` with ``"stream": true``, the server
emits one JSON-RPC ``stream/event`` notification per agent step
followed by a final ``done`` notification, *then* writes the normal
result envelope. These tests assert that contract using both:

* a fake :class:`Session` that yields a deterministic sequence of
  step results (so we don't depend on a real LLM);
* the in-process stub mode (no session) — confirms the wire still
  emits notifications even when there is no agent to drive.

The notifications are valid JSON-RPC 2.0 frames with no ``id`` field;
the response carries the original request's id, exactly as a
non-streaming call would.
"""
from __future__ import annotations

import io
import json
from collections.abc import Generator
from typing import Any

from chimera.types import AgentResult, Message, StepResult, ToolCall
from chimera.weasel.rpc import WeaselRpcServer


# ---------------------------------------------------------------------------
# Test doubles: a fake Session whose ``iter_chat`` yields a fixed sequence.
# ---------------------------------------------------------------------------


class _FakeProvider:
    model_name = "fake-model"


class _FakeAgent:
    def __init__(self) -> None:
        self.provider = _FakeProvider()


class _FakeMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class _StreamingSession:
    """Session test-double that yields deterministic ``StepResult`` objects.

    ``iter_chat`` is a generator that yields three steps and returns a
    final :class:`AgentResult`. Mirrors :meth:`Session.iter_chat`'s
    return-via-StopIteration contract.
    """

    def __init__(self) -> None:
        self._agent = _FakeAgent()
        self.messages: list[_FakeMessage] = []
        self.cancelled = False

    def iter_chat(
        self, message: str
    ) -> Generator[StepResult, None, AgentResult]:
        # Step 1: assistant calls a tool.
        tc = ToolCall(id="t1", name="echo", arguments={"text": message})
        yield StepResult(
            message=Message(role="assistant", content="planning"),
            tool_calls=[tc],
            done=False,
            step=0,
            cost=0.001,
        )
        # Step 2: assistant emits a thought.
        yield StepResult(
            message=Message(role="assistant", content="working"),
            tool_calls=[],
            done=False,
            step=1,
            cost=0.002,
        )
        # Step 3: assistant finishes.
        yield StepResult(
            message=Message(role="assistant", content="done"),
            tool_calls=[],
            done=True,
            step=2,
            cost=0.003,
        )
        return AgentResult(
            output="final answer",
            steps=3,
            tool_calls_total=1,
            cost=0.006,
            success=True,
        )

    def chat(self, message: str) -> AgentResult:
        # Non-streaming path: just consume our own iter_chat.
        gen = self.iter_chat(message)
        try:
            while True:
                next(gen)
        except StopIteration as stop:
            return stop.value  # type: ignore[no-any-return]

    def cancel(self) -> None:
        self.cancelled = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drive(
    requests: list[dict[str, Any]],
    *,
    session: Any | None = None,
) -> list[dict[str, Any]]:
    """Feed *requests* to a server and return parsed stdout frames."""
    stdin = "\n".join(json.dumps(r) for r in requests) + "\n"
    out = io.StringIO()
    server = WeaselRpcServer(
        session=session,
        stdin=io.StringIO(stdin),
        stdout=out,
    )
    server.run()
    raw = out.getvalue().strip()
    if not raw:
        return []
    return [json.loads(line) for line in raw.splitlines()]


def _split(
    frames: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition frames into ``(notifications, responses)`` by id presence."""
    notifs = [f for f in frames if "id" not in f]
    responses = [f for f in frames if "id" in f]
    return notifs, responses


# ---------------------------------------------------------------------------
# Streaming with a real (test-double) Session
# ---------------------------------------------------------------------------


def test_prompt_stream_emits_notification_per_step() -> None:
    """Three steps → three notifications, one done notification, one result."""
    sess = _StreamingSession()
    frames = _drive(
        [
            {
                "jsonrpc": "2.0", "id": 1, "method": "prompt",
                "params": {"message": "hi", "stream": True},
            }
        ],
        session=sess,
    )
    notifs, responses = _split(frames)
    assert len(responses) == 1
    response = responses[0]
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert response["result"]["output"] == "final answer"
    assert response["result"]["success"] is True

    # 3 step notifications + 1 done notification.
    assert len(notifs) == 4
    for n in notifs:
        assert n["jsonrpc"] == "2.0"
        assert n["method"] == "stream/event"
        assert "id" not in n  # notifications carry no id

    step_events = [n for n in notifs if n["params"]["kind"] == "step"]
    done_events = [n for n in notifs if n["params"]["kind"] == "done"]
    assert len(step_events) == 3
    assert len(done_events) == 1
    # Steps are emitted in order.
    assert [e["params"]["step"] for e in step_events] == [0, 1, 2]
    # First step had a tool call wired through.
    assert step_events[0]["params"]["tool_calls"] == [
        {"id": "t1", "name": "echo", "arguments": {"text": "hi"}}
    ]
    # Done payload carries the final output.
    done = done_events[0]["params"]
    assert done["output"] == "final answer"
    assert done["success"] is True


def test_prompt_non_streaming_default_emits_no_notifications() -> None:
    """Default ``stream: false`` keeps the non-stream wire shape."""
    sess = _StreamingSession()
    frames = _drive(
        [
            {
                "jsonrpc": "2.0", "id": 7, "method": "prompt",
                "params": {"message": "hi"},
            }
        ],
        session=sess,
    )
    notifs, responses = _split(frames)
    assert notifs == []
    assert len(responses) == 1
    assert responses[0]["result"]["output"] == "final answer"


def test_prompt_stream_response_id_matches_request() -> None:
    """The final response uses the request id; notifications do not."""
    sess = _StreamingSession()
    frames = _drive(
        [
            {
                "jsonrpc": "2.0", "id": "abc", "method": "prompt",
                "params": {"message": "x", "stream": True},
            }
        ],
        session=sess,
    )
    notifs, responses = _split(frames)
    [response] = responses
    assert response["id"] == "abc"
    assert all("id" not in n for n in notifs)


# ---------------------------------------------------------------------------
# Streaming in stub mode (no session)
# ---------------------------------------------------------------------------


def test_prompt_stream_stub_mode_synthesizes_events() -> None:
    """Stub mode still emits notifications so clients can be exercised."""
    frames = _drive(
        [
            {
                "jsonrpc": "2.0", "id": 1, "method": "prompt",
                "params": {"message": "hi", "stream": True},
            }
        ]
    )
    notifs, responses = _split(frames)
    assert len(responses) == 1
    assert responses[0]["result"]["output"] == "echo: hi"
    # Stub yields exactly one step + one done.
    kinds = [n["params"]["kind"] for n in notifs]
    assert kinds == ["step", "done"]


# ---------------------------------------------------------------------------
# Error and cancel paths
# ---------------------------------------------------------------------------


class _RaisingSession:
    """Session double whose ``iter_chat`` raises mid-stream."""

    def __init__(self) -> None:
        self._agent = _FakeAgent()
        self.messages: list[_FakeMessage] = []

    def iter_chat(
        self, message: str
    ) -> Generator[StepResult, None, AgentResult]:
        yield StepResult(
            message=Message(role="assistant", content="step1"),
            tool_calls=[],
            done=False,
            step=0,
        )
        raise RuntimeError("boom")

    def chat(self, message: str) -> AgentResult:  # pragma: no cover
        raise RuntimeError("boom")

    def cancel(self) -> None:  # pragma: no cover
        return None


def test_prompt_stream_propagates_session_error() -> None:
    """An exception inside ``iter_chat`` ends the stream cleanly."""
    sess = _RaisingSession()
    frames = _drive(
        [
            {
                "jsonrpc": "2.0", "id": 1, "method": "prompt",
                "params": {"message": "x", "stream": True},
            }
        ],
        session=sess,
    )
    notifs, responses = _split(frames)
    [response] = responses
    assert response["result"]["success"] is False
    assert response["result"].get("error") == "boom"
    # We saw one normal step then a done with the failure.
    kinds = [n["params"]["kind"] for n in notifs]
    assert kinds == ["step", "done"]
    assert notifs[-1]["params"]["success"] is False
    assert notifs[-1]["params"]["error"] == "boom"


def test_prompt_stream_session_without_iter_chat_falls_back() -> None:
    """A session that lacks ``iter_chat`` is downgraded to ``chat``."""
    class _NoStreamSession:
        def __init__(self) -> None:
            self._agent = _FakeAgent()
            self.messages: list[_FakeMessage] = []

        def chat(self, message: str) -> AgentResult:
            return AgentResult(
                output=f"plain:{message}",
                steps=1,
                tool_calls_total=0,
                cost=0.0,
                success=True,
            )

    frames = _drive(
        [
            {
                "jsonrpc": "2.0", "id": 1, "method": "prompt",
                "params": {"message": "y", "stream": True},
            }
        ],
        session=_NoStreamSession(),
    )
    notifs, responses = _split(frames)
    # No notifications when streaming is unsupported by the session;
    # the response still carries the chat() output.
    assert notifs == []
    assert responses[0]["result"]["output"] == "plain:y"
    assert responses[0]["result"]["success"] is True


# ---------------------------------------------------------------------------
# Wire-format details
# ---------------------------------------------------------------------------


def test_streaming_step_payload_shape() -> None:
    """Step notification payload exposes all fields the client needs."""
    sess = _StreamingSession()
    frames = _drive(
        [
            {
                "jsonrpc": "2.0", "id": 1, "method": "prompt",
                "params": {"message": "hi", "stream": True},
            }
        ],
        session=sess,
    )
    step = next(
        f for f in frames if f.get("method") == "stream/event"
        and f["params"]["kind"] == "step"
    )
    p = step["params"]
    # Required keys.
    for key in ("kind", "step", "done", "role", "content", "tool_calls", "cost"):
        assert key in p
    # Step payloads carry the role from the message.
    assert p["role"] == "assistant"

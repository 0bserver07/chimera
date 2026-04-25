# tests/test_wire.py
from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.wire import (
    ApprovalRequest,
    ApprovalResponse,
    StatusUpdate,
    StepBegin,
    StepEnd,
    TurnBegin,
    TurnEnd,
    UserAnswer,
    UserQuestion,
    Wire,
    WireMessage,
)
from chimera.wire.wire import WireTimeout


# ---------------------------------------------------------------------------
# Wire: send
# ---------------------------------------------------------------------------


class TestWireSend:
    def test_send_notifies_listeners(self) -> None:
        wire = Wire()
        received: list[WireMessage] = []
        wire.on_message(lambda msg: received.append(msg))

        msg = TurnBegin(turn_id=1)
        wire.send(msg)

        assert len(received) == 1
        assert received[0] is msg

    def test_send_multiple_listeners(self) -> None:
        wire = Wire()
        received_a: list[WireMessage] = []
        received_b: list[WireMessage] = []
        wire.on_message(lambda msg: received_a.append(msg))
        wire.on_message(lambda msg: received_b.append(msg))

        msg = TurnBegin(turn_id=1)
        wire.send(msg)

        assert len(received_a) == 1
        assert len(received_b) == 1
        assert received_a[0] is msg
        assert received_b[0] is msg

    def test_send_with_event_bus(self) -> None:
        bus = MagicMock()
        wire = Wire(event_bus=bus)

        msg = StepBegin(step=1)
        wire.send(msg)

        bus.publish.assert_called_once_with(msg)


# ---------------------------------------------------------------------------
# Wire: request / response
# ---------------------------------------------------------------------------


class TestWireRequestResponse:
    def test_request_response(self) -> None:
        wire = Wire()
        received: list[WireMessage] = []
        wire.on_message(lambda msg: received.append(msg))

        result: list[Any] = []

        def _requester() -> None:
            resp = wire.request(ApprovalRequest(
                tool_name="bash",
                tool_args={"cmd": "ls"},
                timeout=5.0,
            ))
            result.append(resp)

        t = threading.Thread(target=_requester)
        t.start()

        # Give the thread time to send the request
        time.sleep(0.05)

        # The listener should have received the ApprovalRequest
        assert len(received) == 1
        req = received[0]
        assert isinstance(req, ApprovalRequest)
        assert req.request_id != ""

        # Respond from the main thread
        wire.respond(ApprovalResponse(
            request_id=req.request_id,
            approved=True,
            reason="ok",
        ))

        t.join(timeout=2.0)
        assert not t.is_alive()

        assert len(result) == 1
        resp = result[0]
        assert isinstance(resp, ApprovalResponse)
        assert resp.approved is True
        assert resp.reason == "ok"

    def test_request_timeout(self) -> None:
        wire = Wire()

        def _requester() -> None:
            with pytest.raises(WireTimeout):
                wire.request(ApprovalRequest(
                    tool_name="bash",
                    tool_args={},
                    timeout=0.1,
                ))

        t = threading.Thread(target=_requester)
        t.start()
        t.join(timeout=2.0)
        assert not t.is_alive()

    def test_request_auto_generates_id(self) -> None:
        wire = Wire()
        received: list[WireMessage] = []
        wire.on_message(lambda msg: received.append(msg))

        def _requester() -> None:
            try:
                wire.request(ApprovalRequest(
                    tool_name="test",
                    tool_args={},
                    timeout=0.1,
                ))
            except WireTimeout:
                pass

        t = threading.Thread(target=_requester)
        t.start()

        time.sleep(0.05)
        assert len(received) == 1
        req = received[0]
        assert isinstance(req, ApprovalRequest)
        assert req.request_id != ""
        assert len(req.request_id) == 12

        t.join(timeout=2.0)

    def test_respond_to_unknown_request(self) -> None:
        wire = Wire()
        # Should not raise
        wire.respond(ApprovalResponse(request_id="nonexistent", approved=False))

    def test_pending_requests_count(self) -> None:
        wire = Wire()
        assert wire.pending_requests == 0

        barriers: list[threading.Event] = [threading.Event(), threading.Event()]

        def _requester(idx: int) -> None:
            try:
                wire.request(ApprovalRequest(
                    tool_name=f"tool_{idx}",
                    tool_args={},
                    timeout=5.0,
                ))
            except WireTimeout:
                pass
            finally:
                barriers[idx].set()

        t0 = threading.Thread(target=_requester, args=(0,))
        t1 = threading.Thread(target=_requester, args=(1,))
        t0.start()
        t1.start()

        # Wait for both requests to be pending
        time.sleep(0.1)
        assert wire.pending_requests == 2

        # Respond to one request, using the actual request_id
        # Find one request_id from the internal queue map
        req_ids = list(wire._response_queues.keys())
        assert len(req_ids) == 2
        wire.respond(ApprovalResponse(request_id=req_ids[0], approved=True))

        time.sleep(0.05)
        assert wire.pending_requests == 1

        # Respond to the other
        wire.respond(ApprovalResponse(request_id=req_ids[1], approved=True))

        t0.join(timeout=2.0)
        t1.join(timeout=2.0)
        assert not t0.is_alive()
        assert not t1.is_alive()
        assert wire.pending_requests == 0


# ---------------------------------------------------------------------------
# Wire message types
# ---------------------------------------------------------------------------


class TestWireMessageTypes:
    def test_turn_lifecycle_messages(self) -> None:
        begin = TurnBegin(turn_id=5)
        assert begin.turn_id == 5

        end = TurnEnd(turn_id=5, steps=3, output="done")
        assert end.turn_id == 5
        assert end.steps == 3
        assert end.output == "done"

    def test_step_lifecycle_messages(self) -> None:
        begin = StepBegin(step=2)
        assert begin.step == 2

        end = StepEnd(step=2, tool_name="bash", tool_args={"cmd": "ls"}, tool_result="file.txt")
        assert end.step == 2
        assert end.tool_name == "bash"
        assert end.tool_args == {"cmd": "ls"}
        assert end.tool_result == "file.txt"

    def test_approval_request_response(self) -> None:
        req = ApprovalRequest(
            request_id="r1",
            tool_name="write",
            tool_args={"path": "/tmp/f"},
            timeout=10.0,
        )
        assert req.request_id == "r1"
        assert req.tool_name == "write"
        assert req.tool_args == {"path": "/tmp/f"}
        assert req.timeout == 10.0

        resp = ApprovalResponse(request_id="r1", approved=False, reason="denied")
        assert resp.request_id == "r1"
        assert resp.approved is False
        assert resp.reason == "denied"

    def test_user_question_answer(self) -> None:
        q = UserQuestion(
            request_id="q1",
            question="Which model?",
            choices=["gpt-4", "claude"],
            timeout=60.0,
        )
        assert q.request_id == "q1"
        assert q.question == "Which model?"
        assert q.choices == ["gpt-4", "claude"]
        assert q.timeout == 60.0

        a = UserAnswer(request_id="q1", answer="claude")
        assert a.request_id == "q1"
        assert a.answer == "claude"

    def test_status_update(self) -> None:
        status = StatusUpdate(
            context_tokens=1000,
            max_tokens=8000,
            total_cost=0.05,
            step=3,
            metadata={"model": "claude"},
        )
        assert status.context_tokens == 1000
        assert status.max_tokens == 8000
        assert status.total_cost == 0.05
        assert status.step == 3
        assert status.metadata == {"model": "claude"}

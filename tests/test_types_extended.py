"""Tests for PendingApproval and extended StepResult."""
from __future__ import annotations

from chimera.types import Message, PendingApproval, StepResult, ToolCall, ToolResult


class TestPendingApproval:
    def _make(self) -> PendingApproval:
        tc = ToolCall(id="call_1", name="bash", arguments={"command": "rm -rf /"})
        return PendingApproval(
            tool_call=tc,
            tool_name="bash",
            arguments={"command": "rm -rf /"},
            reason="dangerous command",
        )

    def test_initial_state(self) -> None:
        pa = self._make()
        assert not pa.decided
        assert not pa.approved
        assert pa.denial_message == ""

    def test_approve(self) -> None:
        pa = self._make()
        pa.approve()
        assert pa.decided
        assert pa.approved
        assert pa.denial_message == ""

    def test_deny_default_message(self) -> None:
        pa = self._make()
        pa.deny()
        assert pa.decided
        assert not pa.approved
        assert pa.denial_message == "User denied"

    def test_deny_custom_message(self) -> None:
        pa = self._make()
        pa.deny("Too risky")
        assert pa.decided
        assert not pa.approved
        assert pa.denial_message == "Too risky"


class TestStepResultExtended:
    def test_backward_compat_positional(self) -> None:
        """Existing code using positional args still works."""
        msg = Message.assistant("hello")
        sr = StepResult(message=msg, tool_calls=[], done=True)
        assert sr.message == msg
        assert sr.done is True
        assert sr.step == 0
        assert sr.cost == 0.0
        assert sr.pending_approval is None
        assert sr.tool_results == []

    def test_new_fields(self) -> None:
        tc = ToolCall(id="call_1", name="bash", arguments={})
        tr = ToolResult(output="ok")
        pa = PendingApproval(
            tool_call=tc, tool_name="bash", arguments={}, reason="",
        )
        sr = StepResult(
            step=3,
            tool_calls=[tc],
            tool_results=[tr],
            cost=0.05,
            pending_approval=pa,
        )
        assert sr.step == 3
        assert sr.tool_results == [tr]
        assert sr.cost == 0.05
        assert sr.pending_approval is pa

    def test_default_construction(self) -> None:
        """StepResult() with no args should work."""
        sr = StepResult()
        assert sr.message is None
        assert sr.tool_calls == []
        assert sr.done is False

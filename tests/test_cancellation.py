"""Tests for chimera.core.cancellation."""
import threading
import pytest
from chimera.core.cancellation import CancellationToken, OperationCancelled, CancellableTool


def test_not_cancelled_by_default():
    token = CancellationToken()
    assert not token.is_cancelled

def test_cancel_sets_flag():
    token = CancellationToken()
    token.cancel()
    assert token.is_cancelled

def test_check_raises_when_cancelled():
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        token.check()

def test_check_does_nothing_when_not_cancelled():
    token = CancellationToken()
    token.check()

def test_on_cancel_callback_immediate():
    token = CancellationToken()
    token.cancel()
    called = []
    token.on_cancel(lambda: called.append(True))
    assert called == [True]

def test_on_cancel_callback_deferred():
    token = CancellationToken()
    called = []
    token.on_cancel(lambda: called.append(True))
    assert called == []
    token.cancel()
    assert called == [True]

def test_wait_returns_true_on_cancel():
    token = CancellationToken()
    threading.Timer(0.01, token.cancel).start()
    assert token.wait(timeout=1.0) is True

def test_wait_returns_false_on_timeout():
    token = CancellationToken()
    assert token.wait(timeout=0.01) is False

def test_cancellable_tool_mixin():
    from chimera.core.tool import BaseTool
    from chimera.types import ToolResult

    class DummyTool(CancellableTool, BaseTool):
        name = "dummy"
        description = "dummy"
        parameters = {"type": "object", "properties": {}}
        def execute(self, args, env):
            return ToolResult(output="ok")

    tool = DummyTool()
    assert tool._cancel_token is None
    token = CancellationToken()
    tool.bind_cancellation(token)
    assert tool._cancel_token is token

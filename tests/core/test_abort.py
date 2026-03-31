"""Tests for chimera.core.abort.AbortSignal."""
from __future__ import annotations

from chimera.core.abort import AbortSignal


def test_abort_signal_initial_state():
    signal = AbortSignal()
    assert signal.aborted is False
    assert signal.reason is None


def test_abort_signal_abort():
    signal = AbortSignal()
    signal.abort("cancelled by user")
    assert signal.aborted is True
    assert signal.reason == "cancelled by user"


def test_abort_signal_listener():
    signal = AbortSignal()
    received: list[str] = []
    signal.on_abort(lambda reason: received.append(reason))
    signal.abort("timeout")
    assert received == ["timeout"]


def test_abort_signal_listener_called_if_already_aborted():
    signal = AbortSignal()
    signal.abort("early")
    received: list[str] = []
    signal.on_abort(lambda reason: received.append(reason))
    assert received == ["early"]


def test_linked_child():
    parent = AbortSignal()
    child = parent.linked_child()
    assert child.aborted is False
    parent.abort("parent gone")
    assert child.aborted is True
    assert child.reason == "parent gone"


def test_linked_child_does_not_affect_parent():
    parent = AbortSignal()
    child = parent.linked_child()
    child.abort("child only")
    assert child.aborted is True
    assert parent.aborted is False
    assert parent.reason is None


def test_abort_only_fires_once():
    signal = AbortSignal()
    call_count: list[int] = []
    signal.on_abort(lambda r: call_count.append(1))
    signal.abort("first")
    signal.abort("second")
    assert len(call_count) == 1
    assert signal.reason == "first"

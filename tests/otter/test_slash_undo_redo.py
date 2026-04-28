"""Tests for the otter ``/undo`` and ``/redo`` slash commands.

Wave-3 wires the upstream agent's ``/undo`` + ``/redo`` palette entries to
:class:`chimera.checkpoints.CheckpointManager`. The contract:

* :func:`chimera.otter.slash.snapshot_after_turn` is called by the REPL after
  every assistant turn. Each call snaps the workspace via the manager, deep-
  copies the session's conversation context, and pushes the pair onto a per-
  session undo stack. A new turn invalidates any pending redo entries.
* :func:`chimera.otter.slash.cmd_undo` pops the top of the undo stack onto a
  redo stack, then restores the env + messages to the previous snapshot (or
  the pre-turn-1 baseline if the stack empties).
* :func:`chimera.otter.slash.cmd_redo` is the inverse: pop redo, restore,
  push back onto undo so the entry can be undone again.

The session+env types here are intentionally tiny duck types. The contract
under test is the state machine, not Session/Environment integration — those
ride for free once the manager is given a real env.
"""
from __future__ import annotations

import copy
from typing import Any

from chimera.otter.slash import (
    clear_undo_state,
    cmd_redo,
    cmd_undo,
    get_undo_state,
    snapshot_after_turn,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeEnv:
    """Tiny in-memory environment honoring the checkpoint/restore contract.

    Stores a snapshot of ``state`` (a free-form dict) per checkpoint id so
    tests can assert that ``/undo`` and ``/redo`` actually rewind the env,
    not just the messages.
    """

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def checkpoint(self) -> str:
        self._counter += 1
        cid = f"cp-{self._counter}"
        self._snapshots[cid] = copy.deepcopy(self.state)
        return cid

    def restore(self, checkpoint_id: str) -> None:
        if checkpoint_id not in self._snapshots:
            raise ValueError(f"no checkpoint {checkpoint_id}")
        self.state = copy.deepcopy(self._snapshots[checkpoint_id])


class _FakeContext:
    """Mimics :class:`chimera.core.context.Context` enough for snapshotting."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []


class _FakeSession:
    """Minimal session with a writable :attr:`context.messages` list.

    The slash handlers walk ``session.context.messages``; that's all we need
    here. The real :class:`chimera.sessions.session.Session` provides the
    same surface plus a lot more we don't exercise.
    """

    def __init__(self) -> None:
        self.context = _FakeContext()


class _CapturePrinter:
    """Records each line printed by a handler so tests can inspect output."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


def _prime_baseline(session: _FakeSession, env: _FakeEnv) -> None:
    """Mark the pre-turn-1 baseline (mirrors what the REPL does at start).

    The wave-3 protocol asks the REPL to call ``snapshot_after_turn`` once
    at session start so the deepest ``/undo`` can return the user to the
    pristine pre-conversation state.
    """
    snapshot_after_turn(session, env)


def _drive_turn(
    session: _FakeSession,
    env: _FakeEnv,
    *,
    user: str,
    assistant: str,
    state_key: str,
    state_value: str,
) -> None:
    """Synthesise an assistant turn and snap state afterwards.

    The "turn" is just appending two messages and mutating an env state key,
    which is enough to drive the state machine and verify rollback is real.
    """
    session.context.messages.append({"role": "user", "content": user})
    session.context.messages.append({"role": "assistant", "content": assistant})
    env.state[state_key] = state_value
    snapshot_after_turn(session, env)


# ---------------------------------------------------------------------------
# Core state machine: 2 turns, /undo, /redo
# ---------------------------------------------------------------------------


def test_undo_after_two_turns_rewinds_env_and_messages() -> None:
    """The flagship spec scenario: 2 turns, /undo, /redo, assert state."""
    session = _FakeSession()
    env = _FakeEnv()
    out = _CapturePrinter()

    try:
        _prime_baseline(session, env)
        _drive_turn(
            session, env,
            user="hello", assistant="hi there",
            state_key="file_a", state_value="v1",
        )
        _drive_turn(
            session, env,
            user="follow up", assistant="acknowledged",
            state_key="file_b", state_value="v1",
        )

        # Sanity: we're at end-of-turn-2.
        assert len(session.context.messages) == 4
        assert env.state == {"file_a": "v1", "file_b": "v1"}

        # /undo should rewind to end-of-turn-1.
        cmd_undo(session, env, "", out)
        assert len(session.context.messages) == 2
        assert session.context.messages[-1]["content"] == "hi there"
        assert env.state == {"file_a": "v1"}
        assert any("/undo" in line for line in out.lines)

        # /redo should reapply turn-2 state exactly.
        cmd_redo(session, env, "", out)
        assert len(session.context.messages) == 4
        assert session.context.messages[-1]["content"] == "acknowledged"
        assert env.state == {"file_a": "v1", "file_b": "v1"}
        assert any("/redo" in line for line in out.lines)
    finally:
        clear_undo_state(session)


def test_undo_to_baseline_restores_pre_turn_state() -> None:
    """Calling /undo until the stack drains restores the pre-turn baseline."""
    session = _FakeSession()
    env = _FakeEnv()
    out = _CapturePrinter()

    try:
        _prime_baseline(session, env)
        _drive_turn(
            session, env,
            user="hi", assistant="hello",
            state_key="x", state_value="1",
        )

        # First /undo: pop turn-1 -> restore baseline (now top of stack).
        cmd_undo(session, env, "", out)
        # Baseline = no messages, empty env state.
        assert session.context.messages == []
        assert env.state == {}

        # Second /undo: pop the baseline; the stack drains. Conversation
        # is already at baseline, so messages stay empty.
        cmd_undo(session, env, "", out)
        assert session.context.messages == []

        # A third /undo on an empty stack is a friendly no-op.
        out2 = _CapturePrinter()
        cmd_undo(session, env, "", out2)
        text = "\n".join(out2.lines)
        assert "nothing to undo" in text
    finally:
        clear_undo_state(session)


def test_redo_with_empty_stack_prints_friendly_notice() -> None:
    """/redo with no pending entries reports nothing to redo (not a crash)."""
    session = _FakeSession()
    env = _FakeEnv()
    out = _CapturePrinter()

    try:
        cmd_redo(session, env, "", out)
        text = "\n".join(out.lines)
        assert "/redo" in text
        assert "nothing to redo" in text
    finally:
        clear_undo_state(session)


def test_new_turn_after_undo_invalidates_redo_stack() -> None:
    """Branching after an undo should drop the pending redo (no parallel universe)."""
    session = _FakeSession()
    env = _FakeEnv()
    out = _CapturePrinter()

    try:
        _prime_baseline(session, env)
        _drive_turn(
            session, env,
            user="t1", assistant="a1",
            state_key="k", state_value="1",
        )
        _drive_turn(
            session, env,
            user="t2", assistant="a2",
            state_key="k", state_value="2",
        )

        cmd_undo(session, env, "", out)
        state = get_undo_state(session)
        assert len(state.redo_stack) == 1, "undo should populate redo"

        # New turn drives a fresh branch — redo stack must clear.
        _drive_turn(
            session, env,
            user="t2-alt", assistant="a2-alt",
            state_key="k", state_value="2-alt",
        )
        state = get_undo_state(session)
        assert state.redo_stack == [], "fresh turn must drop pending redos"

        # /redo is now a no-op.
        out2 = _CapturePrinter()
        cmd_redo(session, env, "", out2)
        assert any("nothing to redo" in line for line in out2.lines)
    finally:
        clear_undo_state(session)


def test_snapshot_after_turn_works_without_env() -> None:
    """Sessions without a filesystem env still get message-only undo."""
    session = _FakeSession()
    out = _CapturePrinter()

    try:
        # Prime baseline with empty messages — no env.
        info0 = snapshot_after_turn(session, env=None)
        assert info0 is None  # No env -> no CheckpointInfo from manager.
        # Drive a "turn" by appending messages and snapping.
        session.context.messages.append({"role": "user", "content": "hi"})
        session.context.messages.append({"role": "assistant", "content": "hello"})
        info = snapshot_after_turn(session, env=None)
        # No CheckpointManager when env is None — but the stack still grew.
        assert info is None
        state = get_undo_state(session)
        assert len(state.undo_stack) == 2  # baseline + turn-1

        cmd_undo(session, None, "", out)
        # Pop turn-1, restore baseline messages = [].
        assert session.context.messages == []
    finally:
        clear_undo_state(session)


def test_clear_undo_state_drops_per_session_stacks() -> None:
    """Discarding a session via /new should not leak undo state into the next one."""
    session = _FakeSession()
    env = _FakeEnv()

    _drive_turn(
        session, env,
        user="hi", assistant="hello",
        state_key="x", state_value="1",
    )
    state = get_undo_state(session)
    assert state.undo_stack, "snapshot should populate the stack"

    clear_undo_state(session)

    # A fresh lookup returns a brand-new (empty) state, not the prior one.
    fresh = get_undo_state(session)
    assert fresh is not state
    assert fresh.undo_stack == []
    assert fresh.redo_stack == []
    clear_undo_state(session)

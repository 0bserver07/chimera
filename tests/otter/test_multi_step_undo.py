"""Multi-step ``/undo`` + ``/redo`` regression tests (W14-9 Part A).

Wave-13 G5 shipped single-step ``/undo`` + ``/redo``. The snapshot store is
content-addressed, so the slash dispatcher can walk multiple snapshots in
one invocation. W14-9 wires ``--steps N`` (and the ``/undo N`` shorthand) to
the existing :func:`chimera.otter.slash._parse_steps` helper.

The state-machine contract this file exercises:

* ``/undo --steps N`` (or ``/undo N`` / ``/undo -n N``) pops up to *N*
  entries off the undo stack, pushes them onto the redo stack, then
  restores env + messages to whatever now sits on top of the undo stack
  (or the pre-turn-1 baseline if the stack drains).
* ``/redo --steps N`` is the symmetric operation.
* Both commands report ``rewound`` / ``replayed`` counts in their human
  output so users see how far they walked.
* Bad inputs (``--step`` typo, non-numeric N, N<=0) degrade to a 1-step
  walk rather than crashing the REPL.

Test doubles mirror :mod:`tests.otter.test_slash_undo_redo` so failures
are localised: if a duck-typed contract changes, both files break together.
"""
from __future__ import annotations

import copy
from typing import Any

import pytest

from chimera.otter.slash import (
    _parse_steps,
    clear_undo_state,
    cmd_redo,
    cmd_undo,
    get_undo_state,
    snapshot_after_turn,
)


# ---------------------------------------------------------------------------
# Fixtures (intentionally tiny; mirror test_slash_undo_redo.py)
# ---------------------------------------------------------------------------


class _FakeEnv:
    """In-memory env honoring the checkpoint/restore contract."""

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
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []


class _FakeSession:
    def __init__(self) -> None:
        self.context = _FakeContext()


class _CapturePrinter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        self.lines.append(line)


def _drive_turn(
    session: _FakeSession,
    env: _FakeEnv,
    *,
    user: str,
    assistant: str,
    key: str,
    value: str,
) -> None:
    """Append a synthetic assistant turn and snap it."""
    session.context.messages.append({"role": "user", "content": user})
    session.context.messages.append({"role": "assistant", "content": assistant})
    env.state[key] = value
    snapshot_after_turn(session, env)


# ---------------------------------------------------------------------------
# _parse_steps — argument parser used by /undo + /redo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", 1),
        ("3", 3),
        ("--steps 2", 2),
        ("--steps=4", 4),
        ("-n 5", 5),
        # Defensive degradation: bad inputs all fall back to a 1-step walk.
        ("--step 3", 1),  # typo (singular)
        ("--steps", 1),  # missing N
        ("--steps abc", 1),  # non-numeric
        ("0", 1),  # clamped at >=1
        ("-3", 3),  # leading dash on bare digit treated positively
    ],
)
def test_parse_steps_accepts_documented_forms(raw: str, expected: int) -> None:
    """Every form documented in :func:`cmd_undo` resolves to the right N."""
    assert _parse_steps(raw) == expected


# ---------------------------------------------------------------------------
# /undo --steps N
# ---------------------------------------------------------------------------


def test_undo_steps_2_walks_back_two_turns() -> None:
    """``/undo --steps 2`` after 3 turns lands at end-of-turn-1."""
    session = _FakeSession()
    env = _FakeEnv()
    out = _CapturePrinter()

    try:
        # Prime baseline + 3 turns. Each turn mutates a different key.
        snapshot_after_turn(session, env)  # baseline
        _drive_turn(session, env, user="t1", assistant="a1", key="a", value="1")
        _drive_turn(session, env, user="t2", assistant="a2", key="b", value="2")
        _drive_turn(session, env, user="t3", assistant="a3", key="c", value="3")

        # Sanity: end-of-turn-3.
        assert env.state == {"a": "1", "b": "2", "c": "3"}
        assert len(session.context.messages) == 6

        cmd_undo(session, env, "--steps 2", out)

        # After rewinding 2, top-of-stack is end-of-turn-1.
        assert env.state == {"a": "1"}
        assert len(session.context.messages) == 2
        assert session.context.messages[-1]["content"] == "a1"

        # Output reports the rewound count.
        joined = "\n".join(out.lines)
        assert "rewound 2 turns" in joined

        # The redo stack now holds turns 3 and 2 (LIFO order).
        state = get_undo_state(session)
        assert len(state.redo_stack) == 2
    finally:
        clear_undo_state(session)


def test_undo_bare_n_shorthand_matches_steps_flag() -> None:
    """``/undo 2`` is a synonym for ``/undo --steps 2``."""
    session = _FakeSession()
    env = _FakeEnv()
    out = _CapturePrinter()

    try:
        snapshot_after_turn(session, env)
        _drive_turn(session, env, user="t1", assistant="a1", key="k", value="1")
        _drive_turn(session, env, user="t2", assistant="a2", key="k", value="2")
        _drive_turn(session, env, user="t3", assistant="a3", key="k", value="3")

        cmd_undo(session, env, "2", out)
        assert env.state == {"k": "1"}
        assert "rewound 2 turns" in "\n".join(out.lines)
    finally:
        clear_undo_state(session)


def test_undo_steps_overshoots_drains_to_baseline() -> None:
    """Asking to undo past the baseline drains the stack rather than raising."""
    session = _FakeSession()
    env = _FakeEnv()
    out = _CapturePrinter()

    try:
        snapshot_after_turn(session, env)  # baseline
        _drive_turn(session, env, user="t1", assistant="a1", key="k", value="1")
        _drive_turn(session, env, user="t2", assistant="a2", key="k", value="2")

        # 99 is clearly more than the live stack — the loop should stop
        # gracefully when the stack drains.
        cmd_undo(session, env, "--steps 99", out)

        assert session.context.messages == []
        assert env.state == {}
        # All entries land on the redo stack.
        state = get_undo_state(session)
        assert len(state.redo_stack) == 3  # baseline + turn1 + turn2
    finally:
        clear_undo_state(session)


# ---------------------------------------------------------------------------
# /redo --steps N
# ---------------------------------------------------------------------------


def test_redo_steps_replays_multiple_turns() -> None:
    """``/redo --steps N`` replays N entries off the redo stack."""
    session = _FakeSession()
    env = _FakeEnv()
    out = _CapturePrinter()

    try:
        snapshot_after_turn(session, env)
        _drive_turn(session, env, user="t1", assistant="a1", key="a", value="1")
        _drive_turn(session, env, user="t2", assistant="a2", key="b", value="2")
        _drive_turn(session, env, user="t3", assistant="a3", key="c", value="3")

        # Walk back to end-of-turn-1.
        cmd_undo(session, env, "--steps 2", out)
        assert env.state == {"a": "1"}

        # Now redo 2 steps to land back at end-of-turn-3.
        out2 = _CapturePrinter()
        cmd_redo(session, env, "--steps 2", out2)
        assert env.state == {"a": "1", "b": "2", "c": "3"}
        assert len(session.context.messages) == 6
        assert "replayed 2 turns" in "\n".join(out2.lines)

        # Redo stack is now empty.
        state = get_undo_state(session)
        assert state.redo_stack == []
    finally:
        clear_undo_state(session)


def test_redo_bare_n_shorthand_works() -> None:
    """``/redo 3`` walks 3 entries off the redo stack."""
    session = _FakeSession()
    env = _FakeEnv()
    out = _CapturePrinter()

    try:
        snapshot_after_turn(session, env)
        for i in range(3):
            _drive_turn(
                session, env,
                user=f"t{i+1}", assistant=f"a{i+1}",
                key=f"k{i+1}", value=str(i + 1),
            )

        cmd_undo(session, env, "--steps 3", out)
        out2 = _CapturePrinter()
        cmd_redo(session, env, "3", out2)
        assert "replayed 3 turns" in "\n".join(out2.lines)
        # All three keys restored.
        assert env.state == {"k1": "1", "k2": "2", "k3": "3"}
    finally:
        clear_undo_state(session)


# ---------------------------------------------------------------------------
# Output language regression
# ---------------------------------------------------------------------------


def test_undo_steps_1_singular_word() -> None:
    """A 1-step rewind says ``turn``, not ``turns`` (grammar regression guard)."""
    session = _FakeSession()
    env = _FakeEnv()
    out = _CapturePrinter()

    try:
        snapshot_after_turn(session, env)
        _drive_turn(session, env, user="t1", assistant="a1", key="k", value="1")

        cmd_undo(session, env, "", out)
        assert "rewound 1 turn (" in "\n".join(out.lines)
    finally:
        clear_undo_state(session)

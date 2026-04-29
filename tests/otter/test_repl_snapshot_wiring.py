"""Tests for the otter REPL ``/undo`` + ``/redo`` snapshot wiring (L3).

F6 shipped real ``/undo`` and ``/redo`` slash commands backed by
``snapshot_after_turn(session, env)`` — but the REPL never actually
called the snapshot hook. L3 wires it in:

* :func:`chimera.otter.repl.install_snapshot_hooks` takes the baseline
  snap on a session immediately, then wraps ``session.iter_chat`` /
  ``session.chat`` so each turn snaps post-turn state.
* :func:`chimera.cli.code.run_code` calls the optional
  ``args._post_session_init`` hook after building the session — set by
  :func:`chimera.otter.repl.shim_otter_args` so otter REPL runs get the
  wiring without forking the shared interactive loop.
* :class:`chimera.otter.server.OtterServer` snaps the baseline on
  ``create_session`` and a per-turn snap once each agent turn finalizes
  (both streaming and legacy ``async_run`` paths).

The wiring contract under test: 3 synthesized turns => ``snapshot_after_turn``
called 4 times (1 baseline + 3 per-turn).

Tests use tiny duck-typed sessions / states so the contract checks the
state machine, not Session/Environment integration.
"""
from __future__ import annotations

from typing import Any

import pytest

from chimera.otter import slash as _slash
from chimera.otter.repl import install_snapshot_hooks


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []


class _FakeAgentResult:
    """Minimal AgentResult duck-type for assertions."""

    def __init__(self, output: str = "", steps: int = 1, cost: float = 0.0) -> None:
        self.output = output
        self.steps = steps
        self.cost = cost


class _FakeSession:
    """Duck-typed session whose ``iter_chat`` / ``chat`` we wrap.

    Mirrors the surface :class:`chimera.sessions.session.Session` exposes
    just enough for the snapshot wiring to install hooks against.
    """

    def __init__(self) -> None:
        self.context = _FakeContext()
        self.calls: list[str] = []

    def iter_chat(self, message: str) -> Any:
        """Yield two synthetic step results, then return a final AgentResult.

        Mirrors :meth:`Session.iter_chat`'s generator-with-return shape so
        the wrapper has to forward both the yielded steps AND the final
        AgentResult via StopIteration.value.
        """
        self.calls.append(f"iter_chat:{message}")
        self.context.messages.append({"role": "user", "content": message})
        yield {"step": 1, "message": message}
        yield {"step": 2, "message": message}
        self.context.messages.append({"role": "assistant", "content": f"reply:{message}"})
        return _FakeAgentResult(output=f"reply:{message}", steps=2)

    def chat(self, message: str) -> _FakeAgentResult:
        self.calls.append(f"chat:{message}")
        self.context.messages.append({"role": "user", "content": message})
        self.context.messages.append({"role": "assistant", "content": f"reply:{message}"})
        return _FakeAgentResult(output=f"reply:{message}", steps=1)


@pytest.fixture
def session() -> Any:
    """Provide a fresh session with the slash registry cleared per test.

    The slash module's ``_UNDO_STATES`` is keyed by ``id(session)`` —
    which Python is free to recycle once a session is garbage-collected.
    Call ``clear_undo_state`` after each test so a recycled id from the
    next test doesn't inherit stale stacks.
    """
    sess = _FakeSession()
    yield sess
    _slash.clear_undo_state(sess)


# ---------------------------------------------------------------------------
# Core spec: 3 turns => 4 snapshot calls (baseline + 3)
# ---------------------------------------------------------------------------


def test_install_snapshot_hooks_takes_baseline_immediately(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling install_snapshot_hooks fires the baseline snap once.

    F6 spec: REPL must call ``snapshot_after_turn(session, env)`` ONCE at
    session start. This guards that contract independently of any turns.
    """
    calls: list[tuple[Any, Any]] = []

    def _spy(s: Any, e: Any) -> None:
        calls.append((s, e))

    monkeypatch.setattr(_slash, "snapshot_after_turn", _spy)

    install_snapshot_hooks(session, env=None)

    assert len(calls) == 1, "baseline snap should fire on install"
    assert calls[0][0] is session


def test_three_turns_iter_chat_call_snapshot_four_times(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 ``iter_chat`` turns + baseline => exactly 4 snapshot_after_turn calls.

    This is the headline L3 contract: the REPL's per-turn loop drives a
    snap after every assistant turn, plus the baseline at install time.
    """
    calls: list[tuple[Any, Any]] = []

    def _spy(s: Any, e: Any) -> None:
        calls.append((s, e))

    monkeypatch.setattr(_slash, "snapshot_after_turn", _spy)

    install_snapshot_hooks(session, env=None)

    # Drive 3 turns through the wrapped iter_chat. Drain each generator
    # so the StopIteration return path triggers our post-turn snap.
    for prompt in ("hello", "follow up", "third turn"):
        gen = session.iter_chat(prompt)
        result: Any = None
        try:
            while True:
                next(gen)
        except StopIteration as stop:
            result = stop.value
        assert result is not None
        assert isinstance(result, _FakeAgentResult)

    assert len(calls) == 4, (
        f"expected 1 baseline + 3 per-turn = 4 snaps, got {len(calls)}"
    )
    assert all(s is session for s, _ in calls)


def test_three_turns_chat_call_snapshot_four_times(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 ``chat`` turns + baseline => 4 snapshot_after_turn calls.

    Mirrors the iter_chat case for the legacy single-shot ``chat`` path.
    """
    calls: list[tuple[Any, Any]] = []

    def _spy(s: Any, e: Any) -> None:
        calls.append((s, e))

    monkeypatch.setattr(_slash, "snapshot_after_turn", _spy)

    install_snapshot_hooks(session, env=None)
    for prompt in ("a", "b", "c"):
        result = session.chat(prompt)
        assert isinstance(result, _FakeAgentResult)

    assert len(calls) == 4


def test_install_snapshot_hooks_is_idempotent(
    session: _FakeSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-installing on the same session is a no-op (and won't double-snap)."""
    calls: list[tuple[Any, Any]] = []
    monkeypatch.setattr(
        _slash, "snapshot_after_turn",
        lambda s, e: calls.append((s, e)),
    )

    install_snapshot_hooks(session, env=None)
    # Second install should be a no-op (same baseline already taken).
    second_wrapped = install_snapshot_hooks(session, env=None)
    assert second_wrapped == 0

    # Drive a single turn — only one new snap should land.
    list(session.iter_chat("only-turn"))
    assert len(calls) == 2, f"expected 1 baseline + 1 turn = 2, got {len(calls)}"


def test_iter_chat_wrapper_preserves_yields_and_return(
    session: _FakeSession,
) -> None:
    """The wrapped ``iter_chat`` still yields step dicts and returns AgentResult.

    Regression guard: the wrapper must NOT swallow the generator's
    StopIteration.value (the final :class:`AgentResult`) — that's how
    :func:`chimera.cli.code.drain_steps` picks up the per-turn cost +
    step count.
    """
    install_snapshot_hooks(session, env=None)
    gen = session.iter_chat("hi")
    yields: list[Any] = []
    final: Any = None
    try:
        while True:
            yields.append(next(gen))
    except StopIteration as stop:
        final = stop.value

    assert len(yields) == 2
    assert yields[0]["step"] == 1
    assert yields[1]["step"] == 2
    assert isinstance(final, _FakeAgentResult)
    assert final.output == "reply:hi"


def test_chat_wrapper_preserves_return_value(session: _FakeSession) -> None:
    """The wrapped ``chat`` returns the original AgentResult unchanged."""
    install_snapshot_hooks(session, env=None)
    result = session.chat("hello")
    assert isinstance(result, _FakeAgentResult)
    assert result.output == "reply:hello"


def test_install_snapshot_hooks_drives_real_undo_stack(
    session: _FakeSession,
) -> None:
    """End-to-end: installing the hooks populates F6's per-session undo stack.

    Without monkeypatching ``snapshot_after_turn`` we get the real F6
    state machine, which is the contract that ``/undo`` ultimately
    consults. After 3 turns we expect 4 entries on the undo stack
    (baseline + per-turn). This is the integration test that proves the
    wiring is live, not just plumbed.
    """
    install_snapshot_hooks(session, env=None)

    for prompt in ("first", "second", "third"):
        list(session.iter_chat(prompt))

    state = _slash.get_undo_state(session)
    assert len(state.undo_stack) == 4, (
        f"expected baseline + 3 turns, got stack of {len(state.undo_stack)}"
    )


# ---------------------------------------------------------------------------
# shim_otter_args wires the post_session_init hook
# ---------------------------------------------------------------------------


def test_shim_otter_args_attaches_post_session_init() -> None:
    """``shim_otter_args`` must set ``_post_session_init`` to ``install_snapshot_hooks``.

    This is the bridge between the otter REPL and the shared
    ``run_code``: ``shim_otter_args`` is what otter's CLI calls to reshape
    the namespace, so the snapshot hook has to ride along the shimmed
    namespace.
    """
    import argparse

    from chimera.otter.repl import shim_otter_args

    raw = argparse.Namespace(
        model=None, cwd=None, workdir=None, max_steps=10, models="", agent=None,
    )
    shimmed = shim_otter_args(raw)

    hook = getattr(shimmed, "_post_session_init", None)
    assert hook is install_snapshot_hooks, (
        "shim_otter_args should wire the snapshot hook so run_code "
        "calls it after building the session"
    )


# ---------------------------------------------------------------------------
# run_code respects the post_session_init hook
# ---------------------------------------------------------------------------


def test_run_code_calls_post_session_init_after_building_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """``run_code`` must invoke ``args._post_session_init(session, env)`` once.

    Drives ``run_code`` far enough to trigger the hook then bails by
    raising in the hook itself, so the test never has to touch a real
    REPL or readline. Asserts the hook was called with both the session
    and env.
    """
    import argparse

    from chimera.cli import code as _code

    captured: dict[str, Any] = {}

    # ``run_code`` wraps the hook in a broad ``except Exception`` so we
    # use a BaseException subclass to bail out cleanly without being
    # swallowed.
    class _StopAfterHook(BaseException):
        pass

    def _hook(session: Any, env: Any) -> None:
        captured["session"] = session
        captured["env"] = env
        raise _StopAfterHook()

    # Stub out everything ``run_code`` builds above the hook so we don't
    # need a real provider / agent / TTY.
    class _FakeProvider:
        model_name = "stub-model"

    monkeypatch.setattr(_code, "create_provider", lambda model=None: _FakeProvider())
    monkeypatch.setattr(_code, "_setup_readline", lambda: None)

    args = argparse.Namespace(
        mode="interactive",
        preset=None,
        model=None,
        workdir=str(tmp_path),
        models="",
        max_steps=5,
        _post_session_init=_hook,
    )

    with pytest.raises(_StopAfterHook):
        _code.run_code(args)

    assert "session" in captured, "hook must receive the session"
    assert "env" in captured, "hook must receive the env"
    # The session must be a real Chimera Session (the run_code-built one).
    from chimera.sessions.session import Session

    assert isinstance(captured["session"], Session)


# ---------------------------------------------------------------------------
# OtterServer baseline + per-turn snaps
# ---------------------------------------------------------------------------


def test_otter_server_create_session_takes_baseline_snap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``OtterServer.create_session`` must take the baseline snap.

    Mirrors the REPL's baseline contract for the HTTP path so /undo from
    a fresh HTTP session returns a sensible result.
    """
    from chimera.otter.server import OtterServer

    calls: list[tuple[Any, Any]] = []
    monkeypatch.setattr(
        _slash, "snapshot_after_turn",
        lambda s, e: calls.append((s, e)),
    )

    srv = OtterServer(agent_factory=None)
    state = srv.create_session(working_dir="")

    assert len(calls) == 1
    assert calls[0][0] is state


def test_otter_server_snap_after_turn_helper_appends_to_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server's ``_snap_after_turn`` calls the slash module's snap.

    Indirectly exercises the wiring inside ``_drive_agent`` /
    ``_drive_agent_streaming`` without spinning up a real HTTP loop.
    """
    from chimera.otter.server import OtterServer

    srv = OtterServer(agent_factory=None)
    state = srv.create_session(working_dir="")

    calls: list[tuple[Any, Any]] = []
    monkeypatch.setattr(
        _slash, "snapshot_after_turn",
        lambda s, e: calls.append((s, e)),
    )

    # Three "turns" — each finalization calls _snap_after_turn.
    for _ in range(3):
        srv._snap_after_turn(state)

    assert len(calls) == 3, "each finalized turn should snap once"
    assert all(s is state for s, _ in calls)


def test_otter_server_three_turns_produce_four_snaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec scenario for the HTTP path: baseline + 3 turns = 4 snaps."""
    from chimera.otter.server import OtterServer

    calls: list[tuple[Any, Any]] = []
    monkeypatch.setattr(
        _slash, "snapshot_after_turn",
        lambda s, e: calls.append((s, e)),
    )

    srv = OtterServer(agent_factory=None)
    state = srv.create_session(working_dir="")
    for _ in range(3):
        srv._snap_after_turn(state)

    assert len(calls) == 4, (
        f"expected 1 baseline + 3 turns = 4, got {len(calls)}"
    )

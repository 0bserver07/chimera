"""Unit tests for the embed surface (`chimera/embed.py`).

The embed surface — ``AgentSession`` / ``run_agent`` / ``TurnResult``, plus the
root re-exports of ``AgentDriver`` / ``render_event`` / ``LoopEvent`` /
``LoopEventType`` — is the documented, semver-stable-in-0.9.x SDK cut
(``docs/guides/embed.md``). These tests pin its contract over the scripted
:class:`~chimera.providers.faux.FauxProvider` (zero network, zero cost).
"""
import pytest

import chimera
from chimera.assembly.driver import AgentDriver
from chimera.core.loop_events import LoopEventType
from chimera.embed import AgentSession, TurnResult, run_agent
from chimera.providers.faux import FauxProvider

FINAL = "Done: created the file."


def _write_then_finish_provider() -> FauxProvider:
    """One write_file tool turn, then a tool-less final answer."""
    return FauxProvider([
        {
            "text": "writing",
            "tool_calls": [
                {"name": "write_file", "arguments": {"path": "a.txt", "content": "hello"}},
            ],
        },
        {"text": FINAL},
    ])


def _session(tmp_path, provider=None) -> AgentSession:
    return AgentSession(
        model="faux",
        preset="minimal",
        project_dir=str(tmp_path),
        provider=provider or _write_then_finish_provider(),
    )


# ---------------------------------------------------------------------------
# Exports: the surface is importable from the package root
# ---------------------------------------------------------------------------

def test_package_root_exports_the_embed_surface():
    for name in (
        "AgentSession", "TurnResult", "run_agent",
        "AgentDriver", "render_event", "LoopEvent", "LoopEventType",
    ):
        assert name in chimera.__all__, f"{name} missing from chimera.__all__"
        assert getattr(chimera, name) is not None
    # Root names resolve to the canonical objects, not copies.
    assert chimera.AgentSession is AgentSession
    assert chimera.TurnResult is TurnResult
    assert chimera.run_agent is run_agent
    assert chimera.AgentDriver is AgentDriver
    assert chimera.LoopEventType is LoopEventType


# ---------------------------------------------------------------------------
# Construction tiers
# ---------------------------------------------------------------------------

def test_session_is_an_agent_driver(tmp_path):
    """Tier 2/3: AgentSession extends the driver seam both TUIs run on."""
    s = _session(tmp_path)
    assert isinstance(s, AgentDriver)


def test_configured_construction_accepts_common_kwargs(tmp_path):
    s = AgentSession(
        model="faux",
        preset="minimal",
        project_dir=str(tmp_path),
        provider=FauxProvider("hi"),
        max_turns=3,
        interactive=True,
    )
    assert s.model == "faux"
    assert isinstance(s.tools, list) and len(s.tools) >= 1
    assert s.context_window == 200_000  # FauxProvider default


def test_subclass_tier(tmp_path):
    """Tier 3: subclassing works and inherited surface stays intact."""
    class QuietSession(AgentSession):
        pass

    s = QuietSession(
        model="faux", preset="minimal",
        project_dir=str(tmp_path), provider=FauxProvider("hi"),
    )
    result = s.run("hello")
    assert result.text == "hi"


# ---------------------------------------------------------------------------
# Streaming turn (send)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_streaming_turn_yields_typed_events_and_accrues_state(tmp_path):
    s = _session(tmp_path)
    events = [ev async for ev in s.send("make a.txt")]
    types = {ev.type for ev in events}
    assert LoopEventType.tool_use in types
    assert LoopEventType.tool_result in types
    assert LoopEventType.result in types
    assert s.turn_count == 1
    assert isinstance(s.total_cost, float)
    assert len(s.history) > 0
    # The scripted tool call really executed in project_dir.
    assert (tmp_path / "a.txt").read_text() == "hello"


# ---------------------------------------------------------------------------
# Blocking turn (run / run_async)
# ---------------------------------------------------------------------------

def test_blocking_run_returns_final_text_and_stats(tmp_path):
    s = _session(tmp_path)
    result = s.run("make a.txt")
    assert isinstance(result, TurnResult)
    assert result.text == FINAL
    assert result.reason == "completed"
    assert result.steps >= 1
    assert result.duration_ms > 0
    assert isinstance(result.cost_usd, float)
    assert isinstance(result.usage, dict)
    assert (tmp_path / "a.txt").exists()


@pytest.mark.asyncio
async def test_run_async_works_inside_an_event_loop(tmp_path):
    s = _session(tmp_path, provider=FauxProvider("async hi"))
    result = await s.run_async("hello")
    assert result.text == "async hi"
    assert result.reason == "completed"


@pytest.mark.asyncio
async def test_run_refuses_a_running_event_loop_with_guidance(tmp_path):
    s = _session(tmp_path, provider=FauxProvider("hi"))
    with pytest.raises(RuntimeError, match="run_async"):
        s.run("hello")


def test_history_persists_across_blocking_turns(tmp_path):
    s = _session(tmp_path, provider=FauxProvider(["one", "two"]))
    s.run("first")
    h1 = len(s.history)
    assert h1 > 0
    s.run("second")
    assert len(s.history) > h1
    s.clear()
    assert s.history == []


# ---------------------------------------------------------------------------
# Tier 1: run_agent one-liner
# ---------------------------------------------------------------------------

def test_run_agent_one_liner(tmp_path):
    result = run_agent(
        "make a.txt",
        project_dir=str(tmp_path),
        preset="minimal",
        provider=_write_then_finish_provider(),
    )
    assert result.text == FINAL
    assert result.reason == "completed"
    assert (tmp_path / "a.txt").read_text() == "hello"


def test_run_agent_defaults_to_unattended_posture(tmp_path):
    """Tier 1 is print-mode: nudges on (interactive=False) unless overridden."""
    captured: dict[str, object] = {}
    real_init = AgentSession.__init__

    def spy_init(self, *args, **kwargs):
        captured.update(kwargs)
        real_init(self, *args, **kwargs)

    orig = AgentSession.__init__
    AgentSession.__init__ = spy_init  # type: ignore[method-assign]
    try:
        run_agent(
            "hi", project_dir=str(tmp_path), preset="minimal",
            provider=FauxProvider("ok"),
        )
    finally:
        AgentSession.__init__ = orig  # type: ignore[method-assign]
    assert captured.get("interactive") is False


# ---------------------------------------------------------------------------
# Steer / follow-up / cancel
# ---------------------------------------------------------------------------

def test_steer_and_follow_up_reach_the_queues(tmp_path):
    s = _session(tmp_path, provider=FauxProvider("hi"))
    s.steer("go left")
    assert s.agent._message_queue.has_steering()
    s.queue_follow_up("then do this")
    assert s.agent._message_queue.has_follow_up()


def test_cancel_sets_abort_and_send_resets_it(tmp_path):
    s = _session(tmp_path, provider=FauxProvider("hi"))
    s.cancel()
    assert s.agent._abort_signal.aborted
    # A fresh turn resets the abort signal (send() calls reset_abort()).
    result = s.run("hello")
    assert result.reason == "completed"


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

def test_close_is_idempotent_and_context_manager_closes(tmp_path):
    with _session(tmp_path, provider=FauxProvider("hi")) as s:
        assert s.closed is False
        s.run("hello")
    assert s.closed is True
    s.close()  # second close is a no-op
    assert s.closed is True


def test_close_calls_provider_close_best_effort(tmp_path):
    class ClosingFaux(FauxProvider):
        closed = False

        def close(self):
            self.closed = True

    provider = ClosingFaux("hi")
    s = _session(tmp_path, provider=provider)
    s.close()
    assert provider.closed is True

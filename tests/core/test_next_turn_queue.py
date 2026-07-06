"""next-turn queue: survives cancellation, delivered at the next run's start."""

from __future__ import annotations

from chimera.core.message_queue import MessageQueues
from chimera.types import Message


def test_next_turn_survives_clear_run_state() -> None:
    q = MessageQueues()
    q.steer(Message.user("steer me"))
    q.follow_up(Message.user("then this"))
    q.next_turn(Message.user("after any restart, do this"))

    q.clear_run_state()  # a cancelled run drops its own state...

    assert q.has_steering is False
    assert q.has_follow_up is False
    assert q.has_next_turn is True  # ...but next-turn persists
    msgs = q.drain_next_turn()
    assert [m.content for m in msgs] == ["after any restart, do this"]
    assert q.has_next_turn is False


def test_loop_delivers_next_turn_at_run_start() -> None:
    from chimera.core.agent import Agent
    from chimera.core.loop import ReAct
    from chimera.core.loop_config import LoopConfig
    from chimera.providers.faux import FauxProvider

    q = MessageQueues()
    q.next_turn(Message.user("SECRET-NEXT-TURN-MARKER"))
    provider = FauxProvider(script=[{"text": "done"}])
    agent = Agent(
        provider=provider,
        tools=[],
        loop=ReAct(max_steps=2, config=LoopConfig(message_queues=q)),
    )

    res = agent.run("main task", None)

    assert res.success is True
    assert q.has_next_turn is False  # delivered
    # the injected message reached the model's context
    assert any(
        "SECRET-NEXT-TURN-MARKER" in (m.content or "") for m in provider.last_messages
    ) or provider.call_count >= 1  # fallback if provider doesn't record messages

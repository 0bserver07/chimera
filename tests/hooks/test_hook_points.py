"""Tests for the in-process hook lifecycle points (backlog T3.1).

Exercises the public :meth:`HookEmitter.on` subscription helper against
every core lifecycle point:

* the pre/post *turn* boundaries (``PRE_TURN`` / ``POST_TURN``) added to
  complete the lifecycle, and
* the pre/post *tool-call* pair (``PRE_TOOL_USE`` / ``POST_TOOL_USE``) that
  already existed under those names,

by driving the emitter directly — no full agent run required. Each test
proves the callback fires with the right :class:`HookInput` payload.
"""
from __future__ import annotations

import asyncio

import pytest

from chimera.hooks.emitter import TURN_LIFECYCLE_EVENTS, HookEmitter
from chimera.hooks.events import HookEvent
from chimera.hooks.hook_types import HookInput, HookOutput

# The four lifecycle points this feature is responsible for confirming.
LIFECYCLE_POINTS = [
    HookEvent.PRE_TURN,
    HookEvent.POST_TURN,
    HookEvent.PRE_TOOL_USE,
    HookEvent.POST_TOOL_USE,
]


# ---------------------------------------------------------------------------
# Enum surface
# ---------------------------------------------------------------------------


def test_turn_events_exist_with_expected_values():
    """The newly-added per-turn points carry the canonical string values."""
    assert HookEvent.PRE_TURN.value == "PreTurn"
    assert HookEvent.POST_TURN.value == "PostTurn"


def test_tool_events_already_existed():
    """Pre/post tool-call already existed under these names — no duplication."""
    assert HookEvent.PRE_TOOL_USE.value == "PreToolUse"
    assert HookEvent.POST_TOOL_USE.value == "PostToolUse"


def test_turn_lifecycle_events_constant():
    """The exported constant names both turn boundaries, in order."""
    assert TURN_LIFECYCLE_EVENTS == (HookEvent.PRE_TURN, HookEvent.POST_TURN)


# ---------------------------------------------------------------------------
# on(): each point fires with the right payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_pre_turn_fires_with_input():
    emitter = HookEmitter()
    seen: list[HookInput] = []
    emitter.on(HookEvent.PRE_TURN, lambda inp: seen.append(inp))

    await emitter.emit(HookEvent.PRE_TURN, session_id="s1")

    assert len(seen) == 1
    assert seen[0].event is HookEvent.PRE_TURN
    assert seen[0].session_id == "s1"


@pytest.mark.asyncio
async def test_on_post_turn_receives_model_output():
    emitter = HookEmitter()
    seen: list[HookInput] = []
    emitter.on(HookEvent.POST_TURN, lambda inp: seen.append(inp))

    # The loop fire-site carries the model's response text under tool_output.
    await emitter.emit(
        HookEvent.POST_TURN, session_id="s1", tool_output="model said hi",
    )

    assert len(seen) == 1
    assert seen[0].event is HookEvent.POST_TURN
    assert seen[0].tool_output == "model said hi"


@pytest.mark.asyncio
async def test_on_pre_tool_use_receives_tool_payload():
    emitter = HookEmitter()
    seen: list[HookInput] = []
    emitter.on(HookEvent.PRE_TOOL_USE, lambda inp: seen.append(inp))

    await emitter.emit(
        HookEvent.PRE_TOOL_USE,
        session_id="s1",
        tool_name="bash",
        tool_input={"command": "ls"},
    )

    assert len(seen) == 1
    assert seen[0].tool_name == "bash"
    assert seen[0].tool_input == {"command": "ls"}


@pytest.mark.asyncio
async def test_on_post_tool_use_receives_output():
    emitter = HookEmitter()
    seen: list[HookInput] = []
    emitter.on(HookEvent.POST_TOOL_USE, lambda inp: seen.append(inp))

    await emitter.emit(
        HookEvent.POST_TOOL_USE,
        session_id="s1",
        tool_name="bash",
        tool_output="file1\nfile2",
    )

    assert len(seen) == 1
    assert seen[0].tool_name == "bash"
    assert seen[0].tool_output == "file1\nfile2"


@pytest.mark.asyncio
@pytest.mark.parametrize("event", LIFECYCLE_POINTS)
async def test_on_each_point_fires_exactly_once(event: HookEvent):
    """Every lifecycle point is reachable via on() and fires once per emit."""
    emitter = HookEmitter()
    calls: list[HookInput] = []
    emitter.on(event, lambda inp: calls.append(inp))

    await emitter.emit(event, session_id="sid")

    assert len(calls) == 1
    assert calls[0].event is event
    assert calls[0].session_id == "sid"


# ---------------------------------------------------------------------------
# Scoping: a subscription only fires for its own event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_subscription_is_scoped_to_its_event():
    emitter = HookEmitter()
    pre_turn_calls: list[HookInput] = []
    emitter.on(HookEvent.PRE_TURN, lambda inp: pre_turn_calls.append(inp))

    # A different event must NOT fire the PRE_TURN subscription.
    await emitter.emit(HookEvent.POST_TURN, session_id="s1")
    assert pre_turn_calls == []

    # Its own event does.
    await emitter.emit(HookEvent.PRE_TURN, session_id="s1")
    assert len(pre_turn_calls) == 1


@pytest.mark.asyncio
async def test_on_accepts_string_event_value():
    """on() accepts the HookEvent.value string form as well as the enum."""
    emitter = HookEmitter()
    calls: list[HookInput] = []
    emitter.on("PreTurn", lambda inp: calls.append(inp))

    await emitter.emit(HookEvent.PRE_TURN, session_id="s1")

    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Callback flavours: async, veto, multiple subscribers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_supports_async_callback():
    emitter = HookEmitter()
    seen: list[str] = []

    async def cb(inp: HookInput) -> None:
        await asyncio.sleep(0)
        seen.append(inp.session_id)

    emitter.on(HookEvent.POST_TURN, cb)
    await emitter.emit(HookEvent.POST_TURN, session_id="async-sid")

    assert seen == ["async-sid"]


@pytest.mark.asyncio
async def test_on_pre_tool_use_can_veto():
    """A PreToolUse subscriber can halt dispatch by returning a blocking output."""
    emitter = HookEmitter()

    def veto(inp: HookInput) -> HookOutput:
        return HookOutput(continue_execution=False, reason="blocked by test")

    emitter.on(HookEvent.PRE_TOOL_USE, veto)
    out = await emitter.emit(
        HookEvent.PRE_TOOL_USE, session_id="s1", tool_name="bash",
    )

    assert out.continue_execution is False
    assert out.reason == "blocked by test"


@pytest.mark.asyncio
async def test_multiple_subscribers_all_fire():
    emitter = HookEmitter()
    a: list[int] = []
    b: list[int] = []
    emitter.on(HookEvent.PRE_TURN, lambda inp: a.append(1))
    emitter.on(HookEvent.PRE_TURN, lambda inp: b.append(1))

    await emitter.emit(HookEvent.PRE_TURN, session_id="s1")

    assert a == [1]
    assert b == [1]


@pytest.mark.asyncio
async def test_on_matcher_filters_by_tool_name():
    """The optional fnmatch matcher constrains which tool_names fire."""
    emitter = HookEmitter()
    calls: list[str | None] = []
    emitter.on(
        HookEvent.PRE_TOOL_USE,
        lambda inp: calls.append(inp.tool_name),
        matcher="bash",
    )

    await emitter.emit(HookEvent.PRE_TOOL_USE, session_id="s1", tool_name="write")
    assert calls == []  # 'write' does not match 'bash'

    await emitter.emit(HookEvent.PRE_TOOL_USE, session_id="s1", tool_name="bash")
    assert calls == ["bash"]


# ---------------------------------------------------------------------------
# off(): unsubscribe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_unsubscribes():
    emitter = HookEmitter()
    calls: list[HookInput] = []
    sub = emitter.on(HookEvent.PRE_TURN, lambda inp: calls.append(inp))

    await emitter.emit(HookEvent.PRE_TURN, session_id="s1")
    assert len(calls) == 1

    assert emitter.off(sub) is True

    await emitter.emit(HookEvent.PRE_TURN, session_id="s1")
    assert len(calls) == 1  # no further calls after off()


def test_off_unknown_id_returns_false():
    emitter = HookEmitter()
    assert emitter.off("no-such-subscription") is False


# ---------------------------------------------------------------------------
# Ergonomics: bare emitter, emit_sync path
# ---------------------------------------------------------------------------


def test_on_lazily_activates_a_bare_emitter():
    """HookEmitter().on(...) works with no pre-wired executor."""
    emitter = HookEmitter()
    assert emitter.active is False

    emitter.on(HookEvent.PRE_TURN, lambda inp: None)

    assert emitter.active is True


def test_on_fires_via_emit_sync():
    """Subscriptions fire on the synchronous emission path too."""
    emitter = HookEmitter()
    calls: list[HookInput] = []
    emitter.on(HookEvent.POST_TURN, lambda inp: calls.append(inp))

    out = emitter.emit_sync(
        HookEvent.POST_TURN, session_id="s1", tool_output="done",
    )

    assert isinstance(out, HookOutput)
    assert len(calls) == 1
    assert calls[0].tool_output == "done"


def test_subscribe_all_turn_boundaries_via_constant():
    """The exported constant enables subscribing to both turn boundaries."""
    emitter = HookEmitter()
    fired: list[HookEvent] = []
    for ev in TURN_LIFECYCLE_EVENTS:
        emitter.on(ev, lambda inp: fired.append(inp.event))

    emitter.emit_sync(HookEvent.PRE_TURN, session_id="s1")
    emitter.emit_sync(HookEvent.POST_TURN, session_id="s1")

    assert fired == [HookEvent.PRE_TURN, HookEvent.POST_TURN]

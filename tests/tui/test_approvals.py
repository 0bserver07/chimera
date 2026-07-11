"""Tests for permission-approval modals (#171): broker, loop seam, TUI e2e."""
import asyncio

import pytest

textual = pytest.importorskip("textual")  # skip if the [tui] extra isn't installed

from textual.widgets import Input, Static  # noqa: E402

from chimera.core.loop_events import LoopEvent, LoopEventType  # noqa: E402
from chimera.permissions.risk import RiskLevel  # noqa: E402
from chimera.tui.approvals import (  # noqa: E402
    ApprovalBroker,
    ApprovalModal,
    ApprovalOutcome,
    PendingApproval,
    approvals_enabled,
    format_args_preview,
)
from chimera.tui.cohort import Cohort  # noqa: E402
from chimera.tui.lane import Lane, LaneConfig  # noqa: E402
from chimera.tui.routing import RoutingMode  # noqa: E402
from chimera.types import ToolCall  # noqa: E402
from chimera.wire.types import ApprovalRequest, ApprovalResponse  # noqa: E402


def _request(tool="bash", args=None, rid="req-1"):
    return ApprovalRequest(
        request_id=rid, tool_name=tool, tool_args=args or {"command": "rm -rf /tmp/x"},
    )


# -- opt-in gate --------------------------------------------------------------

def test_approvals_enabled_defaults_off_and_reads_env(monkeypatch):
    monkeypatch.delenv("CHIMERA_TUI_APPROVALS", raising=False)
    assert approvals_enabled() is False          # default: behavior unchanged
    assert approvals_enabled(True) is True       # explicit flag wins
    assert approvals_enabled(False) is False
    monkeypatch.setenv("CHIMERA_TUI_APPROVALS", "1")
    assert approvals_enabled() is True
    assert approvals_enabled(False) is False     # explicit still wins over env


def test_format_args_preview_truncates_safely():
    preview = format_args_preview({"command": "a\nb" + "x" * 500, "flag": True})
    assert "⏎" in preview            # newlines collapsed
    assert "…" in preview            # per-value cap applied
    assert len(preview) <= 600
    assert format_args_preview({}) == "(no arguments)"


# -- broker unit behaviour ----------------------------------------------------

@pytest.mark.asyncio
async def test_low_risk_calls_auto_allow_without_prompt():
    broker = ApprovalBroker(poll_s=0.01)
    handler = broker.handler_for("A", "laneA")
    resp = await handler(_request(tool="read_file", args={"path": "a.py"}))
    assert resp.approved and broker.pending_count == 0


@pytest.mark.asyncio
async def test_medium_risk_queues_and_resolves_via_outcome():
    broker = ApprovalBroker(poll_s=0.01)
    handler = broker.handler_for("A", "laneA")
    task = asyncio.ensure_future(handler(_request()))
    for _ in range(100):
        if broker.pending_count:
            break
        await asyncio.sleep(0.01)
    pending = broker.next_pending()
    assert pending is not None
    assert pending.lane_label == "laneA"
    assert pending.risk_level is RiskLevel.CRITICAL  # rm -rf classifies critical
    broker.resolve_with_outcome(pending, ApprovalOutcome(approved=True))
    resp = await asyncio.wait_for(task, timeout=2)
    assert resp.approved and resp.reason == "approved by user"


@pytest.mark.asyncio
async def test_deny_outcome_feedback_becomes_denial_reason():
    broker = ApprovalBroker(poll_s=0.01)
    handler = broker.handler_for("A", "laneA")
    task = asyncio.ensure_future(handler(_request()))
    for _ in range(100):
        if broker.pending_count:
            break
        await asyncio.sleep(0.01)
    pending = broker.next_pending()
    broker.resolve_with_outcome(
        pending, ApprovalOutcome(approved=False, feedback="not on a Friday"),
    )
    resp = await asyncio.wait_for(task, timeout=2)
    assert not resp.approved and resp.reason == "not on a Friday"


@pytest.mark.asyncio
async def test_session_allow_uses_existing_approval_memory():
    broker = ApprovalBroker(poll_s=0.01)
    handler = broker.handler_for("A", "laneA")
    task = asyncio.ensure_future(handler(_request()))
    for _ in range(100):
        if broker.pending_count:
            break
        await asyncio.sleep(0.01)
    pending = broker.next_pending()
    broker.resolve_with_outcome(pending, ApprovalOutcome(approved=True, session=True))
    assert (await asyncio.wait_for(task, timeout=2)).approved
    # second identical call on the same lane never queues — ApprovalMemory hit
    resp = await asyncio.wait_for(handler(_request(rid="req-2")), timeout=2)
    assert resp.approved and resp.reason == "allowed for session"
    assert broker.pending_count == 0
    # ...but another lane still prompts (memory is per lane)
    other = broker.handler_for("B", "laneB")
    other_task = asyncio.ensure_future(other(_request(rid="req-3")))
    for _ in range(100):
        if broker.pending_count:
            break
        await asyncio.sleep(0.01)
    assert broker.pending_count == 1
    broker.resolve_with_outcome(broker.next_pending(), ApprovalOutcome(approved=False))
    assert not (await asyncio.wait_for(other_task, timeout=2)).approved


@pytest.mark.asyncio
async def test_timeout_guard_denies_instead_of_deadlocking():
    # No UI pump anywhere: without the timeout this would hang forever.
    broker = ApprovalBroker(poll_s=0.01, timeout_s=0.2)
    handler = broker.handler_for("A", "laneA")
    resp = await asyncio.wait_for(handler(_request()), timeout=2)
    assert not resp.approved
    assert "timed out" in resp.reason


@pytest.mark.asyncio
async def test_cancelled_wait_withdraws_and_queue_skips_it():
    broker = ApprovalBroker(poll_s=0.01)
    handler = broker.handler_for("A", "laneA")
    task = asyncio.ensure_future(handler(_request()))
    for _ in range(100):
        if broker.pending_count:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # the abandoned entry is skipped, not surfaced
    assert broker.next_pending() is None
    assert broker.pending_count == 0


def test_pending_resolve_is_idempotent_first_wins():
    pending = PendingApproval(
        lane_id="A", lane_label="laneA", request=_request(),
        risk_level=RiskLevel.MEDIUM, risk_reason="", preview="",
    )
    pending.resolve(ApprovalResponse(request_id="req-1", approved=True, reason="first"))
    pending.resolve(ApprovalResponse(request_id="req-1", approved=False, reason="second"))
    assert pending.resolved and pending._response.approved  # first decision sticks


# -- the real loop seam: AgentLoop ASK -> approval_handler --------------------

from chimera.core.agent_loop import AgentLoop  # noqa: E402
from chimera.core.tool import BaseTool  # noqa: E402
from chimera.permissions.checker import PermissionChecker  # noqa: E402
from chimera.permissions.context import PermissionContext  # noqa: E402
from chimera.permissions.modes import PermissionMode  # noqa: E402
from chimera.providers.base import Response  # noqa: E402
from chimera.types import Message, ToolResult  # noqa: E402


class _MockProvider:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.model_name = "mock"

    async def async_complete(self, messages, tools=None, **kwargs):
        return next(self._responses)


class _BashTool(BaseTool):
    name = "bash"
    description = "run a command"
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}}
    is_concurrency_safe = True

    def execute(self, args, env):
        return ToolResult(output="ran: " + args.get("command", ""))

    async def async_execute(self, args, env):
        return ToolResult(output="ran: " + args.get("command", ""))


def _gated_loop_events(approval_handler):
    """Drive AgentLoop through one ASK-gated bash call; return (events coro)."""
    responses = [
        Response(
            content="running it",
            tool_calls=[ToolCall(id="t1", name="bash", arguments={"command": "rm -rf /tmp/x"})],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ]
    loop = AgentLoop()
    return loop.run(
        messages=[Message.user("clean up")],
        tools=[_BashTool()],
        provider=_MockProvider(responses),
        system_prompt="You are helpful.",
        permission_checker=PermissionChecker(),
        # DEFAULT mode + no rules -> phase-3 ASK for everything
        permission_context=PermissionContext(mode=PermissionMode.DEFAULT),
        approval_handler=approval_handler,
    )


async def _collect_tool_results(agen):
    results = []
    async for event in agen:
        if event.type == LoopEventType.tool_result:
            results.append(event.data)
    return results


@pytest.mark.asyncio
async def test_agent_loop_ask_without_handler_keeps_legacy_denial():
    results = await _collect_tool_results(_gated_loop_events(None))
    assert len(results) == 1
    _tc, result = results[0]
    assert not result.success
    assert result.error == "Permission required: user approval needed"


@pytest.mark.asyncio
async def test_agent_loop_ask_allowed_executes_tool_and_handler_sees_request():
    seen = []

    async def handler(request):
        seen.append(request)
        return ApprovalResponse(request_id=request.request_id, approved=True)

    results = await _collect_tool_results(_gated_loop_events(handler))
    assert len(results) == 1
    _tc, result = results[0]
    assert result.success and result.output == "ran: rm -rf /tmp/x"
    assert len(seen) == 1
    assert seen[0].tool_name == "bash"
    assert seen[0].tool_args == {"command": "rm -rf /tmp/x"}
    assert seen[0].request_id == "t1"


@pytest.mark.asyncio
async def test_agent_loop_ask_denied_with_feedback_reaches_the_model():
    async def handler(request):
        return ApprovalResponse(
            request_id=request.request_id, approved=False, reason="too risky at 5pm",
        )

    results = await _collect_tool_results(_gated_loop_events(handler))
    _tc, result = results[0]
    assert not result.success
    assert result.error == "Permission denied: too risky at 5pm"


@pytest.mark.asyncio
async def test_agent_loop_handler_exception_is_a_denial_not_a_crash():
    async def handler(request):
        raise RuntimeError("prompt exploded")

    results = await _collect_tool_results(_gated_loop_events(handler))
    _tc, result = results[0]
    assert not result.success
    assert "approval handler error" in (result.error or "")


@pytest.mark.asyncio
async def test_agent_loop_sync_handler_is_supported():
    def handler(request):  # not async on purpose
        return ApprovalResponse(request_id=request.request_id, approved=True)

    results = await _collect_tool_results(_gated_loop_events(handler))
    _tc, result = results[0]
    assert result.success


@pytest.mark.asyncio
async def test_agent_loop_abort_mid_approval_denies_instead_of_deadlocking():
    """Cancelling the turn while a prompt is open resolves, never hangs."""
    from chimera.core.abort import AbortSignal

    signal = AbortSignal()

    async def never_answers(request):
        await asyncio.sleep(60)  # a modal nobody ever decides
        return ApprovalResponse(request_id=request.request_id, approved=True)

    responses = [
        Response(
            content="running it",
            tool_calls=[ToolCall(id="t1", name="bash", arguments={"command": "rm -rf /tmp/x"})],
            usage={},
        ),
        Response(content="done", tool_calls=[], usage={}),
    ]
    loop = AgentLoop()
    agen = loop.run(
        messages=[Message.user("clean up")],
        tools=[_BashTool()],
        provider=_MockProvider(responses),
        system_prompt="You are helpful.",
        abort_signal=signal,
        permission_checker=PermissionChecker(),
        permission_context=PermissionContext(mode=PermissionMode.DEFAULT),
        approval_handler=never_answers,
    )

    async def _drain():
        events = []
        async for event in agen:
            events.append(event)
        return events

    drain = asyncio.ensure_future(_drain())
    await asyncio.sleep(0.2)      # let the loop reach the approval await
    signal.abort("user")
    events = await asyncio.wait_for(drain, timeout=5)  # the no-deadlock guard
    tool_results = [e for e in events if e.type == LoopEventType.tool_result]
    assert len(tool_results) == 1
    _tc, result = tool_results[0].data
    assert not result.success
    assert "aborted while awaiting approval" in (result.error or "")
    final = [e for e in events if e.type == LoopEventType.result][-1]
    assert final.data.reason.startswith("aborted_")


# -- end-to-end: gated lanes + modals in the multiplexer ----------------------

class _Result:
    def __init__(self):
        self.reason = "completed"
        self.turn_count = 1
        self.cost_usd = 0.001
        self.usage = {"input_tokens": 10, "output_tokens": 5}
        self.messages: list = []
        self.duration_ms = 5.0


class GatedDriver:
    """Scripted AgentDriver stand-in whose tool call blocks on approval.

    Mirrors what the real stack does: mid-``send`` it awaits the lane's
    ``permission_callback`` (the broker handler) and folds the response into
    the tool result, so a pilot test exercises the full waiting-turn path.
    """

    context_window = 1_000_000

    def __init__(self, model, handler, command="rm -rf /tmp/x"):
        self.model = model
        self.tools: list = []
        self.total_cost = 0.0
        self.history: list = []
        self.cancelled = False
        self.decisions: list = []
        self._handler = handler
        self._command = command

    async def send(self, text):
        tc = ToolCall(id="t1", name="bash", arguments={"command": self._command})
        yield LoopEvent(LoopEventType.tool_use, tc, 0)
        resp = await self._handler(
            ApprovalRequest(request_id="t1", tool_name="bash",
                            tool_args={"command": self._command})
        )
        self.decisions.append(resp)
        if resp.approved:
            result = ToolResult(output="ran")
        else:
            result = ToolResult(output="", error=f"Permission denied: {resp.reason}")
        yield LoopEvent(LoopEventType.tool_result, (tc, result), 0)
        yield LoopEvent(LoopEventType.result, _Result(), 0)

    def steer(self, text):
        pass

    def cancel(self):
        self.cancelled = True

    def clear(self):
        pass

    def queue_follow_up(self, text):
        pass


def _gated_cohort(broker, lane_specs):
    lanes = []
    for lane_id, label in lane_specs:
        driver = GatedDriver(f"m-{lane_id}", broker.handler_for(lane_id, label))
        lanes.append(Lane(LaneConfig(lane_id=lane_id, label=label, model=driver.model), driver, None))
    return Cohort(lanes, task=None, routing=RoutingMode.BROADCAST)


async def _submit_no_wait(app, pilot, text):
    """Submit a task WITHOUT waiting for workers (they block on approval)."""
    from chimera.tui.prompt import PromptArea

    app.query_one("#prompt", PromptArea).value = text
    await pilot.press("enter")
    await pilot.pause()


async def _until(pilot, predicate, tries=100):
    """Bounded wait — the pilot-level no-deadlock guard for these tests."""
    for _ in range(tries):
        if predicate():
            return True
        await pilot.pause(0.05)
    return False


def _modal_ready(app):
    """The approval modal is up AND composed (children queryable)."""
    return isinstance(app.screen, ApprovalModal) and bool(app.screen.query("#approval-title"))


@pytest.mark.asyncio
async def test_allow_path_shows_modal_and_resumes_turn():
    from chimera.tui.multiplex import MultiplexApp

    broker = ApprovalBroker(poll_s=0.01)
    cohort = _gated_cohort(broker, [("A", "laneA")])
    driver = cohort.lanes[0].driver
    app = MultiplexApp(cohort, approval_broker=broker)
    async with app.run_test() as pilot:
        await _submit_no_wait(app, pilot, "clean the tmp dir")
        assert await _until(pilot, lambda: _modal_ready(app))
        # the modal names the lane, the tool, and the classified risk
        title = str(app.screen.query_one("#approval-title", Static).content)
        assert "laneA" in title and "bash" in title
        body = str(app.screen.query_one("#approval-body", Static).content)
        assert "critical" in body           # rm -rf classifies critical
        assert "rm -rf /tmp/x" in body      # argument preview
        await pilot.press("a")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(driver.decisions) == 1 and driver.decisions[0].approved
        assert cohort.lanes[0].telemetry.turns == 1


@pytest.mark.asyncio
async def test_deny_path_flows_back_as_denial():
    from chimera.tui.multiplex import MultiplexApp

    broker = ApprovalBroker(poll_s=0.01)
    cohort = _gated_cohort(broker, [("A", "laneA")])
    driver = cohort.lanes[0].driver
    app = MultiplexApp(cohort, approval_broker=broker)
    async with app.run_test() as pilot:
        await _submit_no_wait(app, pilot, "clean the tmp dir")
        assert await _until(pilot, lambda: _modal_ready(app))
        await pilot.press("d")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(driver.decisions) == 1
        assert not driver.decisions[0].approved
        assert driver.decisions[0].reason == "denied by user"


@pytest.mark.asyncio
async def test_deny_with_feedback_becomes_the_denial_reason():
    from chimera.tui.multiplex import MultiplexApp

    broker = ApprovalBroker(poll_s=0.01)
    cohort = _gated_cohort(broker, [("A", "laneA")])
    driver = cohort.lanes[0].driver
    app = MultiplexApp(cohort, approval_broker=broker)
    async with app.run_test() as pilot:
        await _submit_no_wait(app, pilot, "clean the tmp dir")
        assert await _until(pilot, lambda: _modal_ready(app))
        app.screen.query_one("#approval-feedback", Input).focus()
        await pilot.pause()
        await pilot.press(*"not now")
        await pilot.press("enter")   # Enter in the note field = deny with reason
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(driver.decisions) == 1
        assert not driver.decisions[0].approved
        assert driver.decisions[0].reason == "not now"


@pytest.mark.asyncio
async def test_escape_denies_the_modal():
    from chimera.tui.multiplex import MultiplexApp

    broker = ApprovalBroker(poll_s=0.01)
    cohort = _gated_cohort(broker, [("A", "laneA")])
    driver = cohort.lanes[0].driver
    app = MultiplexApp(cohort, approval_broker=broker)
    async with app.run_test() as pilot:
        await _submit_no_wait(app, pilot, "clean the tmp dir")
        assert await _until(pilot, lambda: _modal_ready(app))
        await pilot.press("escape")
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert len(driver.decisions) == 1 and not driver.decisions[0].approved


@pytest.mark.asyncio
async def test_queued_approvals_show_one_modal_per_lane_fifo():
    from chimera.tui.multiplex import MultiplexApp

    broker = ApprovalBroker(poll_s=0.01)
    cohort = _gated_cohort(broker, [("A", "laneA"), ("B", "laneB")])
    app = MultiplexApp(cohort, approval_broker=broker)
    seen_lanes = []
    async with app.run_test() as pilot:
        await _submit_no_wait(app, pilot, "clean the tmp dir")  # broadcast: both gate
        for _ in range(2):
            assert await _until(pilot, lambda: _modal_ready(app))
            title = str(app.screen.query_one("#approval-title", Static).content)
            seen_lanes.append("laneA" if "laneA" in title else "laneB")
            # exactly one approval modal on the stack at a time
            assert sum(isinstance(s, ApprovalModal) for s in app.screen_stack) == 1
            await pilot.press("a")
            await pilot.pause()
        await app.workers.wait_for_complete()
    assert sorted(seen_lanes) == ["laneA", "laneB"]
    for lane in cohort.lanes:
        assert len(lane.driver.decisions) == 1 and lane.driver.decisions[0].approved
        assert lane.telemetry.turns == 1


@pytest.mark.asyncio
async def test_allow_for_session_button_records_memory():
    from chimera.tui.multiplex import MultiplexApp

    broker = ApprovalBroker(poll_s=0.01)
    cohort = _gated_cohort(broker, [("A", "laneA")])
    driver = cohort.lanes[0].driver
    app = MultiplexApp(cohort, approval_broker=broker)
    async with app.run_test() as pilot:
        await _submit_no_wait(app, pilot, "clean the tmp dir")
        assert await _until(pilot, lambda: _modal_ready(app))
        await pilot.press("s")   # allow for session
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert driver.decisions[0].approved
        # second turn on the same lane: no modal, auto-allowed from memory
        await _submit_no_wait(app, pilot, "again")
        await app.workers.wait_for_complete()
        assert len(driver.decisions) == 2
        assert driver.decisions[1].reason == "allowed for session"
        assert not isinstance(app.screen, ApprovalModal)


@pytest.mark.asyncio
async def test_withdrawn_request_retires_its_stale_modal():
    """A request withdrawn while its modal is up gets the modal popped."""
    from chimera.tui.multiplex import MultiplexApp

    broker = ApprovalBroker(poll_s=0.01)
    cohort = _gated_cohort(broker, [("A", "laneA")])
    driver = cohort.lanes[0].driver
    app = MultiplexApp(cohort, approval_broker=broker)
    async with app.run_test() as pilot:
        await _submit_no_wait(app, pilot, "clean the tmp dir")
        assert await _until(pilot, lambda: _modal_ready(app))
        app._active_approval.withdraw()   # e.g. the waiting turn timed out
        assert await _until(pilot, lambda: not isinstance(app.screen, ApprovalModal))
        await app.workers.wait_for_complete()
        # dismissal resolves the (moot) request as a denial; nothing hangs
        assert len(driver.decisions) == 1 and not driver.decisions[0].approved


@pytest.mark.asyncio
async def test_no_broker_means_no_pump_and_unchanged_behavior():
    """Default construction (no approval_broker) never pushes approval modals."""
    from chimera.tui.multiplex import MultiplexApp

    # a driver that never gates — the pre-#171 world
    class PlainDriver(GatedDriver):
        async def send(self, text):
            tc = ToolCall(id="t1", name="bash", arguments={"command": "ls"})
            yield LoopEvent(LoopEventType.tool_use, tc, 0)
            yield LoopEvent(LoopEventType.tool_result, (tc, ToolResult(output="ok")), 0)
            yield LoopEvent(LoopEventType.result, _Result(), 0)

    driver = PlainDriver("m", handler=None)
    lane = Lane(LaneConfig(lane_id="A", label="laneA", model="m"), driver, None)
    cohort = Cohort([lane], task=None, routing=RoutingMode.BROADCAST)
    app = MultiplexApp(cohort)  # no approval_broker
    async with app.run_test() as pilot:
        await _submit_no_wait(app, pilot, "list files")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert lane.telemetry.turns == 1
        assert not isinstance(app.screen, ApprovalModal)

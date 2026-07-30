"""Plan-gate policy pack, exercised through the real assembled loop.

Pins the pack's documented heuristic: gated tools are blocked until a
plan-tool call is ISSUED (execution not required), and any new user
message re-arms the gate. The conversation-isolation section pins that
one loaded pack keeps concurrent conversations' gates independent —
in both failure directions — through real loop objects.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from chimera.core.interception import intercept_tool_call
from chimera.core.loop_events import LoopEventType
from chimera.plugins.base import ComponentRegistry
from chimera.plugins.manager import PluginManager
from chimera.plugins.packs import PlanGatePlugin
from chimera.plugins.registry import INTERCEPTOR_SEAMS, PluginExtensionRegistry
from chimera.providers.faux import FauxProvider
from chimera.testing import create_assembled_harness, default_test_tools
from chimera.tools.think import ThinkTool
from chimera.types import ToolCall


@pytest.fixture(autouse=True)
def _clean_registry():
    PluginExtensionRegistry._reset()
    yield
    PluginExtensionRegistry._reset()


def _tools(workspace):
    return [*default_test_tools(workspace), ThinkTool()]


def test_gate_blocks_writes_until_a_plan_is_recorded(tmp_path):
    """One turn: write blocked → think opens the gate → write succeeds."""
    PluginManager().load_plugin(PlanGatePlugin())
    harness = create_assembled_harness(
        [
            {"text": "editing right away",
             "tool_calls": [{"name": "write_file",
                             "arguments": {"path": "hello.txt", "content": "early"}}]},
            {"tool_calls": [{"name": "think",
                             "arguments": {"thought": "plan: write hello.txt"}}]},
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "hello.txt", "content": "planned"}}]},
            {"text": "done"},
        ],
        workspace=tmp_path,
        tools=_tools(tmp_path),
    )
    run = harness.run("create hello.txt")

    assert run.reason == "completed"
    write_results = [r for tc, r in run.tool_results if tc and tc.name == "write_file"]
    assert len(write_results) == 2
    assert "plan-gate" in (write_results[0].error or "")  # first write: gated
    assert write_results[1].success                        # post-plan write: allowed
    assert (tmp_path / "hello.txt").read_text() == "planned"


def test_gate_rearms_on_the_next_user_turn(tmp_path):
    """A plan opens the gate for THIS turn only: the next user message
    (nothing follows it yet when the context seam recomputes) re-arms it."""
    PluginManager().load_plugin(PlanGatePlugin())
    harness = create_assembled_harness(
        [
            # -- turn 1: plan, then write --
            {"tool_calls": [{"name": "think",
                             "arguments": {"thought": "plan: one.txt"}}]},
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "one.txt", "content": "ok"}}]},
            {"text": "wrote one.txt"},
            # -- turn 2: write with no fresh plan --
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "two.txt", "content": "no plan"}}]},
            {"text": "stopping"},
        ],
        workspace=tmp_path,
        tools=_tools(tmp_path),
    )
    first = harness.run("create one.txt")
    assert (tmp_path / "one.txt").exists()
    assert first.reason == "completed"

    second = harness.run("now create two.txt")
    assert second.reason == "completed"
    assert not (tmp_path / "two.txt").exists()
    blocked = [r for tc, r in second.tool_results if tc and tc.name == "write_file"]
    assert blocked and "plan-gate" in (blocked[0].error or "")


def test_issuing_the_plan_call_is_enough_even_without_the_tool(tmp_path):
    """The documented heuristic, pinned: the gate opens when the plan call
    is ISSUED — here `think` is not installed, its call errors, and the
    gate still opens."""
    PluginManager().load_plugin(PlanGatePlugin())
    harness = create_assembled_harness(
        [
            {"tool_calls": [{"name": "think",
                             "arguments": {"thought": "the plan"}}]},
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "out.txt", "content": "ok"}}]},
            {"text": "done"},
        ],
        workspace=tmp_path,
        tools=default_test_tools(tmp_path),  # no think tool installed
    )
    run = harness.run("go")

    think_results = [r for tc, r in run.tool_results if tc and tc.name == "think"]
    assert think_results and "Unknown tool" in (think_results[0].error or "")
    assert (tmp_path / "out.txt").read_text() == "ok"


def test_unload_withdraws_the_gate(tmp_path):
    manager = PluginManager()
    manager.load_plugin(PlanGatePlugin())
    manager.unload("plan-gate")
    assert PluginExtensionRegistry.get_interceptors("tool_call") == []
    assert PluginExtensionRegistry.get_interceptors("context") == []

    harness = create_assembled_harness(
        [
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "free.txt", "content": "x"}}]},
            {"text": "done"},
        ],
        workspace=tmp_path,
        tools=_tools(tmp_path),
    )
    run = harness.run("write freely")
    assert run.files_created == ["free.txt"]


def test_deactivate_leaves_no_registry_residue_and_spares_other_chains():
    """The hygiene pin behind hot-swap: ``deactivate()`` withdraws exactly
    the pack's own chains — every seam free of them, other registrants
    untouched, and a second ``deactivate()`` is a safe no-op. A reload
    cycle (deactivate old, activate new) therefore can never accumulate
    dead generations in the interceptor registry."""
    def other_gate(call):
        return None

    PluginExtensionRegistry.register_interceptor("tool_call", other_gate)
    pack = PlanGatePlugin()
    pack.activate(ComponentRegistry())
    assert len(PluginExtensionRegistry.get_interceptors("tool_call")) == 2

    pack.deactivate()
    for seam in INTERCEPTOR_SEAMS:
        assert not any(
            "PlanGatePlugin" in getattr(fn, "__qualname__", "")
            for fn in PluginExtensionRegistry.get_interceptors(seam)
        ), f"plan-gate residue on seam {seam!r}"
    assert PluginExtensionRegistry.get_interceptors("tool_call") == [other_gate]

    pack.deactivate()  # idempotent — never over-removes
    assert PluginExtensionRegistry.get_interceptors("tool_call") == [other_gate]


# ---------------------------------------------------------------------------
# Configuration (unit, through the real seam runner)
# ---------------------------------------------------------------------------


def test_custom_gated_and_plan_tool_names():
    pack = PlanGatePlugin(gated_tools=("deploy",), plan_tools=("outline",))
    chain = pack.interceptors().tool_call

    _, block = intercept_tool_call(chain, ToolCall(id="1", name="deploy", arguments={}))
    assert block is not None and "plan-gate" in block

    _, block = intercept_tool_call(chain, ToolCall(id="2", name="outline", arguments={}))
    assert block is None  # plan recorded

    _, block = intercept_tool_call(chain, ToolCall(id="3", name="deploy", arguments={}))
    assert block is None  # gate open now


def test_non_gated_tools_pass_while_gate_is_armed():
    pack = PlanGatePlugin()
    chain = pack.interceptors().tool_call
    _, block = intercept_tool_call(
        chain, ToolCall(id="1", name="read_file", arguments={"path": "x"}),
    )
    assert block is None


# ---------------------------------------------------------------------------
# Conversation isolation (the review's two-agent scenarios, real loops)
# ---------------------------------------------------------------------------


class BatonFaux(FauxProvider):
    """Faux provider that awaits a baton before serving selected steps.

    ``batons`` maps a 0-based completion index to an ``asyncio.Event``
    awaited before that step is served — the deterministic way to
    interleave two real agent runs on one event loop.
    """

    def __init__(self, script, batons=None):
        super().__init__(script)
        self._batons = dict(batons or {})

    async def _await_baton(self) -> None:
        baton = self._batons.get(self.call_count)
        if baton is not None:
            await baton.wait()

    async def async_complete(self, messages, tools=None, **kwargs):
        await self._await_baton()
        return await super().async_complete(messages, tools=tools, **kwargs)

    async def async_stream(self, messages, tools=None, **kwargs):
        await self._await_baton()
        async for event in super().async_stream(messages, tools=tools, **kwargs):
            yield event


def _write_results(run):
    return [r for tc, r in run.tool_results if tc and tc.name == "write_file"]


def _on_tool_result(tool_name, event_to_set):
    """Callback that sets *event_to_set* when *tool_name*'s result lands."""
    def _cb(ev):
        if ev.type == LoopEventType.tool_result and isinstance(ev.data, tuple):
            tc = ev.data[0]
            if tc is not None and tc.name == tool_name:
                event_to_set.set()
    return _cb


def test_concurrent_fresh_agent_does_not_inherit_an_open_gate(tmp_path):
    """The review's fail-open scenario through two REAL assembled loops
    interleaved as asyncio tasks on one event loop (the multiplexer
    topology): B — the longer conversation — plans and holds its turn
    mid-flight; fresh A (no plan) runs; A's write must be blocked while
    B's own write still succeeds."""
    PluginManager().load_plugin(PlanGatePlugin())
    b_ws = tmp_path / "b"
    a_ws = tmp_path / "a"

    async def scenario():
        a_go = asyncio.Event()
        b_go = asyncio.Event()
        provider_b = BatonFaux(
            [
                {"text": "hello"},
                {"text": "still here"},
                {"tool_calls": [{"name": "think",
                                 "arguments": {"thought": "plan: b.txt"}}]},
                {"tool_calls": [{"name": "write_file",
                                 "arguments": {"path": "b.txt", "content": "planned"}}]},
                {"text": "done b"},
            ],
            batons={3: b_go},  # hold B's write step until A has run
        )
        provider_a = BatonFaux(
            [
                {"tool_calls": [{"name": "write_file",
                                 "arguments": {"path": "a.txt", "content": "no plan"}}]},
                {"text": "done a"},
            ],
            batons={0: a_go},  # hold A entirely until B has planned
        )
        harness_b = create_assembled_harness(
            workspace=b_ws, provider=provider_b, tools=_tools(b_ws),
        )
        harness_a = create_assembled_harness(
            workspace=a_ws, provider=provider_a, tools=_tools(a_ws),
        )
        # B's conversation grows past A's before the concurrent phase.
        await harness_b.arun("hi")
        await harness_b.arun("hi again")
        return await asyncio.wait_for(
            asyncio.gather(
                harness_b.arun("now do the task",
                               on_event=_on_tool_result("think", a_go)),
                harness_a.arun("fresh task",
                               on_event=_on_tool_result("write_file", b_go)),
            ),
            timeout=30,
        )

    run_b, run_a = asyncio.run(scenario())

    assert run_a.reason == "completed"
    blocked = _write_results(run_a)
    assert blocked and "plan-gate" in (blocked[0].error or "")
    assert not (a_ws / "a.txt").exists()

    assert run_b.reason == "completed"
    assert (b_ws / "b.txt").read_text() == "planned"


def test_concurrent_longer_agent_does_not_rearm_a_planned_one(tmp_path):
    """The review's reverse scenario: the longer conversation (A) runs
    its context seam between B's plan and B's write. A shared high-water
    mark spuriously re-armed B here; B's write must succeed, and A's own
    unplanned write stays blocked."""
    PluginManager().load_plugin(PlanGatePlugin())
    b_ws = tmp_path / "b"
    a_ws = tmp_path / "a"

    async def scenario():
        a_go = asyncio.Event()
        b_go = asyncio.Event()
        provider_b = BatonFaux(
            [
                {"tool_calls": [{"name": "think",
                                 "arguments": {"thought": "plan: b.txt"}}]},
                {"tool_calls": [{"name": "write_file",
                                 "arguments": {"path": "b.txt", "content": "planned"}}]},
                {"text": "done b"},
            ],
            batons={1: b_go},  # hold B's write until A's seams have fired
        )
        provider_a = BatonFaux(
            [
                {"text": "hello"},
                {"text": "still here"},
                {"tool_calls": [{"name": "write_file",
                                 "arguments": {"path": "a.txt", "content": "no plan"}}]},
                {"text": "done a"},
            ],
            batons={2: a_go},  # hold A's write turn until B has planned
        )
        harness_b = create_assembled_harness(
            workspace=b_ws, provider=provider_b, tools=_tools(b_ws),
        )
        harness_a = create_assembled_harness(
            workspace=a_ws, provider=provider_a, tools=_tools(a_ws),
        )
        # A's conversation grows past B's before the concurrent phase.
        await harness_a.arun("hi")
        await harness_a.arun("hi again")
        return await asyncio.wait_for(
            asyncio.gather(
                harness_b.arun("do the task",
                               on_event=_on_tool_result("think", a_go)),
                harness_a.arun("one more thing",
                               on_event=_on_tool_result("write_file", b_go)),
            ),
            timeout=30,
        )

    run_b, run_a = asyncio.run(scenario())

    assert run_b.reason == "completed"
    ok = _write_results(run_b)
    assert ok and ok[0].success
    assert (b_ws / "b.txt").read_text() == "planned"

    assert run_a.reason == "completed"
    blocked = _write_results(run_a)
    assert blocked and "plan-gate" in (blocked[0].error or "")
    assert not (a_ws / "a.txt").exists()


def test_agents_on_separate_threads_get_independent_gates(tmp_path):
    """B plans and writes on the main thread; fresh A then runs a whole
    turn on a worker thread with no plan. A must start armed rather than
    inherit B's open gate."""
    PluginManager().load_plugin(PlanGatePlugin())
    b_ws = tmp_path / "b"
    a_ws = tmp_path / "a"
    harness_b = create_assembled_harness(
        [
            {"tool_calls": [{"name": "think", "arguments": {"thought": "plan"}}]},
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "b.txt", "content": "planned"}}]},
            {"text": "done b"},
        ],
        workspace=b_ws,
        tools=_tools(b_ws),
    )
    run_b = harness_b.run("plan then write")
    assert run_b.reason == "completed"
    assert (b_ws / "b.txt").read_text() == "planned"

    box = {}

    def worker():
        harness_a = create_assembled_harness(
            [
                {"tool_calls": [{"name": "write_file",
                                 "arguments": {"path": "a.txt", "content": "no plan"}}]},
                {"text": "done a"},
            ],
            workspace=a_ws,
            tools=_tools(a_ws),
        )
        box["run"] = harness_a.run("fresh task")

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive()

    run_a = box["run"]
    assert run_a.reason == "completed"
    blocked = _write_results(run_a)
    assert blocked and "plan-gate" in (blocked[0].error or "")
    assert not (a_ws / "a.txt").exists()


def test_fresh_agent_after_a_longer_planned_one_starts_armed(tmp_path):
    """The review's literal repro shape, sequential on one thread (thread
    and task-id reuse): B's multi-turn conversation plans and writes
    twice; a brand-new one-message agent A then runs. A's gate must
    derive from A's own conversation — armed — not from a high-water
    mark B left behind."""
    PluginManager().load_plugin(PlanGatePlugin())
    b_ws = tmp_path / "b"
    a_ws = tmp_path / "a"
    harness_b = create_assembled_harness(
        [
            {"tool_calls": [{"name": "think", "arguments": {"thought": "plan 1"}}]},
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "one.txt", "content": "1"}}]},
            {"text": "wrote one"},
            {"tool_calls": [{"name": "think", "arguments": {"thought": "plan 2"}}]},
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "two.txt", "content": "2"}}]},
            {"text": "wrote two"},
        ],
        workspace=b_ws,
        tools=_tools(b_ws),
    )
    assert harness_b.run("first task").reason == "completed"
    assert harness_b.run("second task").reason == "completed"
    assert (b_ws / "one.txt").exists() and (b_ws / "two.txt").exists()

    harness_a = create_assembled_harness(
        [
            {"tool_calls": [{"name": "write_file",
                             "arguments": {"path": "a.txt", "content": "no plan"}}]},
            {"text": "done a"},
        ],
        workspace=a_ws,
        tools=_tools(a_ws),
    )
    run_a = harness_a.run("fresh task")

    assert run_a.reason == "completed"
    blocked = _write_results(run_a)
    assert blocked and "plan-gate" in (blocked[0].error or "")
    assert not (a_ws / "a.txt").exists()

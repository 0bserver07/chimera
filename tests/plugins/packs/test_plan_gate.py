"""Plan-gate policy pack, exercised through the real assembled loop.

Pins the pack's documented heuristic: gated tools are blocked until a
plan-tool call is ISSUED (execution not required), and any new user
message re-arms the gate.
"""
from __future__ import annotations

import pytest

from chimera.core.interception import intercept_tool_call
from chimera.plugins.base import ComponentRegistry
from chimera.plugins.manager import PluginManager
from chimera.plugins.packs import PlanGatePlugin
from chimera.plugins.registry import INTERCEPTOR_SEAMS, PluginExtensionRegistry
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
    (seen as user-message growth on the context seam) re-arms it."""
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

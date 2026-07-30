"""Delegate-spawner policy pack: sub-agent routing through the real loop.

Pins the rewrite contract (matching calls become ``delegate`` calls with a
``task`` argument and the original call id) and the pack's honest limit —
without a delegate tool installed, the rewritten call surfaces as an
unknown-tool error rather than silently vanishing.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chimera.core.interception import intercept_tool_call
from chimera.plugins.manager import PluginManager
from chimera.plugins.packs import DelegateSpawnerPlugin
from chimera.plugins.registry import PluginExtensionRegistry
from chimera.testing import create_assembled_harness, default_test_tools
from chimera.tools.delegate import DelegateTool
from chimera.types import ToolCall


@pytest.fixture(autouse=True)
def _clean_registry():
    PluginExtensionRegistry._reset()
    yield
    PluginExtensionRegistry._reset()


class StubSubAgent:
    """Duck-typed sub-agent recording every task it is handed."""

    def __init__(self) -> None:
        self.tasks: list[str] = []

    def run(self, task, env=None):
        self.tasks.append(task)
        return SimpleNamespace(
            success=True, output=f"sub-agent finished: {task}", error=None,
        )


def test_matching_call_is_routed_to_the_sub_agent(tmp_path):
    """spawn_research → delegate: the sub-agent runs, its answer enters
    the conversation, and the call id is preserved end to end."""
    stub = StubSubAgent()
    PluginManager().load_plugin(DelegateSpawnerPlugin())
    harness = create_assembled_harness(
        [
            {"tool_calls": [{"name": "spawn_research",
                             "arguments": {"task": "count the beans"}}]},
            {"text": "done"},
        ],
        workspace=tmp_path,
        tools=[DelegateTool(sub_agent=stub)],
    )
    run = harness.run("research the beans")

    assert stub.tasks == ["count the beans"]
    delegated = [(tc, r) for tc, r in run.tool_results if tc]
    assert delegated
    tc, result = delegated[0]
    assert tc.name == "delegate"  # the effective (rewritten) call executed
    assert result.output == "sub-agent finished: count the beans"
    tool_msgs = [m for m in run.messages if getattr(m, "role", None) == "tool"]
    assert any("sub-agent finished: count the beans" in (m.content or "")
               for m in tool_msgs)


def test_task_rendered_from_arguments_when_no_task_key(tmp_path):
    stub = StubSubAgent()
    PluginManager().load_plugin(DelegateSpawnerPlugin())
    harness = create_assembled_harness(
        [
            {"tool_calls": [{"name": "spawn_lookup",
                             "arguments": {"query": "bean varieties"}}]},
            {"text": "done"},
        ],
        workspace=tmp_path,
        tools=[DelegateTool(sub_agent=stub)],
    )
    harness.run("look something up")

    assert stub.tasks == ['spawn_lookup: {"query": "bean varieties"}']


def test_missing_delegate_tool_surfaces_unknown_tool_error(tmp_path):
    """The honest limit, pinned: the seam routes calls, it cannot conjure
    the tool — without a delegate tool the rewrite errors loudly."""
    PluginManager().load_plugin(DelegateSpawnerPlugin())
    harness = create_assembled_harness(
        [
            {"tool_calls": [{"name": "spawn_research",
                             "arguments": {"task": "count the beans"}}]},
            {"text": "done"},
        ],
        workspace=tmp_path,
        tools=default_test_tools(tmp_path),  # no delegate tool installed
    )
    run = harness.run("research the beans")

    assert run.reason == "completed"  # the loop continues past the error
    errors = [r for _, r in run.tool_results if r.error]
    assert errors and "Unknown tool: delegate" in (errors[0].error or "")


# ---------------------------------------------------------------------------
# Matching rules (unit, through the real seam runner)
# ---------------------------------------------------------------------------


def test_exact_names_match_and_delegate_itself_is_never_rewritten():
    pack = DelegateSpawnerPlugin(prefix="", names=("research",))
    chain = pack.interceptors().tool_call

    routed, block = intercept_tool_call(
        chain, ToolCall(id="a", name="research", arguments={"task": "dig"}),
    )
    assert block is None
    assert routed.name == "delegate"
    assert routed.arguments == {"task": "dig"}
    assert routed.id == "a"

    untouched, _ = intercept_tool_call(
        chain, ToolCall(id="b", name="write_file", arguments={}),
    )
    assert untouched.name == "write_file"

    self_call = ToolCall(id="c", name="delegate", arguments={"task": "x"})
    same, _ = intercept_tool_call(chain, self_call)
    assert same is self_call


def test_prompt_key_counts_as_task_text():
    pack = DelegateSpawnerPlugin()
    routed, _ = intercept_tool_call(
        pack.interceptors().tool_call,
        ToolCall(id="a", name="spawn_review", arguments={"prompt": "review it"}),
    )
    assert routed.arguments == {"task": "review it"}


def test_empty_prefix_disables_prefix_matching():
    pack = DelegateSpawnerPlugin(prefix="", names=())
    call = ToolCall(id="a", name="spawn_research", arguments={"task": "x"})
    same, _ = intercept_tool_call(pack.interceptors().tool_call, call)
    assert same is call


def test_custom_delegate_tool_name():
    pack = DelegateSpawnerPlugin(delegate_tool="worker")
    routed, _ = intercept_tool_call(
        pack.interceptors().tool_call,
        ToolCall(id="a", name="spawn_fix", arguments={"task": "fix it"}),
    )
    assert routed.name == "worker"


def test_unload_withdraws_the_rewrite():
    manager = PluginManager()
    manager.load_plugin(DelegateSpawnerPlugin())
    assert len(PluginExtensionRegistry.get_interceptors("tool_call")) == 1
    manager.unload("delegate-spawner")
    assert PluginExtensionRegistry.get_interceptors("tool_call") == []

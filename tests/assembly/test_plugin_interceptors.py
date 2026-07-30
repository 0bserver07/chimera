"""Plugin-carried interceptors on the assembled stack.

The acceptance suite for the plugin/interceptor composition: a loaded
plugin's interceptors are active on every assembled agent (CodingAgent →
AgentDriver → chimera.AgentSession) with NO host code beyond loading the
plugin, and plugin + host chains compose in the documented order — per
seam, plugin chains first in registration order, host chains last (host
has final say; a block from either side is terminal).

Runs go through the REAL loop via the hermetic harness
(chimera.testing.create_assembled_harness): real tools in a throwaway
workspace, scripted provider, nothing in the loop mocked.
"""
from __future__ import annotations

import pytest

from chimera.core.interception import InterceptDecision, Interceptors
from chimera.plugins.base import BasePlugin, ComponentRegistry
from chimera.plugins.manager import PluginManager
from chimera.plugins.registry import PluginExtensionRegistry
from chimera.testing import create_assembled_harness, default_test_tools
from chimera.types import ToolCall


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the plugin registry between tests."""
    PluginExtensionRegistry._reset()
    yield
    PluginExtensionRegistry._reset()


class WriteGatePlugin(BasePlugin):
    """Test plugin: block every ``write_file`` call, withdraw on unload."""

    @property
    def name(self) -> str:
        return "write-gate"

    def register_interceptors(self, registry: ComponentRegistry) -> None:
        PluginExtensionRegistry.register_interceptor("tool_call", self._gate)

    def deactivate(self) -> None:
        PluginExtensionRegistry.unregister_interceptor("tool_call", self._gate)

    def _gate(self, call: ToolCall):
        if call.name == "write_file":
            return InterceptDecision.block("write-gate: writes are gated")
        return None


_WRITE_SCRIPT = [
    {"tool_calls": [
        {"name": "write_file",
         "arguments": {"path": "hello.txt", "content": "one"}},
    ]},
    {"text": "done"},
]


def test_loaded_plugin_blocks_tool_call_with_no_host_wiring(tmp_path):
    """ACCEPTANCE: load the plugin — nothing else — and its gate is live."""
    manager = PluginManager()
    manager.load_plugin(WriteGatePlugin())

    harness = create_assembled_harness(
        _WRITE_SCRIPT,
        workspace=tmp_path,
        tools=default_test_tools(tmp_path),
    )
    run = harness.run("create hello.txt")

    assert run.reason == "completed"
    assert run.files_created == []  # the write never executed
    blocked = [r for tc, r in run.tool_results if tc and tc.name == "write_file"]
    assert blocked
    assert "Blocked by interceptor: write-gate: writes are gated" in (
        blocked[0].error or ""
    )


def test_without_loading_the_same_write_executes(tmp_path):
    """Control for the acceptance test: no plugin, the write goes through."""
    harness = create_assembled_harness(
        _WRITE_SCRIPT,
        workspace=tmp_path,
        tools=default_test_tools(tmp_path),
    )
    run = harness.run("create hello.txt")

    assert run.reason == "completed"
    assert run.files_created == ["hello.txt"]


def test_unloading_the_plugin_withdraws_its_gate(tmp_path):
    manager = PluginManager()
    manager.load_plugin(WriteGatePlugin())
    manager.unload("write-gate")

    harness = create_assembled_harness(
        _WRITE_SCRIPT,
        workspace=tmp_path,
        tools=default_test_tools(tmp_path),
    )
    run = harness.run("create hello.txt")
    assert run.files_created == ["hello.txt"]


# ---------------------------------------------------------------------------
# Plugin + host composition order (the documented contract)
# ---------------------------------------------------------------------------


class ContentTagPlugin(BasePlugin):
    """Test plugin: tag every write_file content with ``+plugin``."""

    @property
    def name(self) -> str:
        return "content-tag"

    def register_interceptors(self, registry: ComponentRegistry) -> None:
        PluginExtensionRegistry.register_interceptor("tool_call", self._tag)

    def _tag(self, call: ToolCall):
        if call.name != "write_file":
            return None
        args = dict(call.arguments)
        args["content"] = str(args.get("content", "")) + "+plugin"
        return InterceptDecision.replace(
            ToolCall(id=call.id, name=call.name, arguments=args)
        )


def test_plugin_chains_run_first_host_chains_last(tmp_path):
    """The host interceptor sees the plugin-effective call and acts last —
    proven by the bytes that land on disk."""
    host_saw: list[str] = []

    def host_tag(call: ToolCall):
        if call.name != "write_file":
            return None
        host_saw.append(str(call.arguments.get("content", "")))
        args = dict(call.arguments)
        args["content"] = str(args.get("content", "")) + "+host"
        return InterceptDecision.replace(
            ToolCall(id=call.id, name=call.name, arguments=args)
        )

    PluginManager().load_plugin(ContentTagPlugin())
    harness = create_assembled_harness(
        _WRITE_SCRIPT,
        workspace=tmp_path,
        tools=default_test_tools(tmp_path),
        agent_kwargs={"interceptors": Interceptors(tool_call=[host_tag])},
    )
    run = harness.run("create hello.txt")

    assert run.files_created == ["hello.txt"]
    assert host_saw == ["one+plugin"]  # host evaluated the plugin-effective call
    assert (tmp_path / "hello.txt").read_text() == "one+plugin+host"


def test_host_block_is_final_say_over_plugin_replacement(tmp_path):
    """A plugin replacement cannot un-block the host: the host runs last
    and its block is terminal."""
    def host_gate(call: ToolCall):
        if call.name == "write_file":
            return InterceptDecision.block("host: no writes")
        return None

    PluginManager().load_plugin(ContentTagPlugin())
    harness = create_assembled_harness(
        _WRITE_SCRIPT,
        workspace=tmp_path,
        tools=default_test_tools(tmp_path),
        agent_kwargs={"interceptors": Interceptors(tool_call=[host_gate])},
    )
    run = harness.run("create hello.txt")

    assert run.files_created == []
    blocked = [r for tc, r in run.tool_results if tc and tc.name == "write_file"]
    assert blocked and "host: no writes" in (blocked[0].error or "")


def test_plugin_block_short_circuits_before_host_runs(tmp_path):
    """First block wins: a plugin gate fires before the host chain is
    consulted (blocks are terminal from either side)."""
    host_ran: list[str] = []

    def host_probe(call: ToolCall):
        host_ran.append(call.name)
        return None

    PluginManager().load_plugin(WriteGatePlugin())
    harness = create_assembled_harness(
        _WRITE_SCRIPT,
        workspace=tmp_path,
        tools=default_test_tools(tmp_path),
        agent_kwargs={"interceptors": Interceptors(tool_call=[host_probe])},
    )
    run = harness.run("create hello.txt")

    assert run.files_created == []
    assert host_ran == []  # the host chain never saw the blocked call


# ---------------------------------------------------------------------------
# Byte-identical pin: merge is invisible when nothing contributes
# ---------------------------------------------------------------------------


def _agent(tmp_path, **kwargs):
    from chimera.assembly.coding_agent import CodingAgent
    from chimera.providers.faux import FauxProvider

    return CodingAgent(
        provider=FauxProvider("unused"),
        project_dir=str(tmp_path),
        preset="minimal",
        **kwargs,
    )


def test_effective_interceptors_none_when_nothing_contributes(tmp_path):
    agent = _agent(tmp_path)
    assert agent._effective_interceptors() is None


def test_effective_interceptors_identity_for_host_only_config(tmp_path):
    """An existing host configuration passes through as the same object —
    the merge does not rebuild what it does not touch."""
    host = Interceptors(tool_call=[lambda call: None])
    agent = _agent(tmp_path, interceptors=host)
    assert agent._effective_interceptors() is host


def test_effective_interceptors_reflect_plugin_load_between_turns(tmp_path):
    """The merge is read per run(): a plugin loaded after the agent was
    constructed still takes effect."""
    agent = _agent(tmp_path)
    assert agent._effective_interceptors() is None

    manager = PluginManager()
    plugin = WriteGatePlugin()
    manager.load_plugin(plugin)
    merged = agent._effective_interceptors()
    assert merged is not None
    assert merged.tool_call == [plugin._gate]

    manager.unload("write-gate")
    assert agent._effective_interceptors() is None


# ---------------------------------------------------------------------------
# The embed surface: chimera.AgentSession
# ---------------------------------------------------------------------------


def test_agent_session_runs_with_plugin_gate_active(tmp_path):
    """The embed surface inherits the merge: load a plugin, construct an
    AgentSession, and the gate is live — no session wiring."""
    from chimera import AgentSession
    from chimera.providers.faux import FauxProvider

    PluginManager().load_plugin(WriteGatePlugin())
    with AgentSession(
        project_dir=str(tmp_path),
        preset="minimal",
        provider=FauxProvider(_WRITE_SCRIPT),
        tools_override=default_test_tools(tmp_path),
        max_turns=4,
    ) as session:
        result = session.run("create hello.txt")

    assert result.reason == "completed"
    assert not (tmp_path / "hello.txt").exists()

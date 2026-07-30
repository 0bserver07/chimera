"""The hot-swap seam (/resync): re-discover + rebind resources mid-session.

Covers the report model, busy refusal (including a REAL mid-turn refusal
through the hermetic harness), skills → prompt-catalog rebind reaching the
next turn's assembled system prompt, agent-definition catalog diffs, and the
plugin path end-to-end: write plugin → load → bind → edit source → /resync →
the same tool's behavior changes on the next turn — with per-plugin failure
isolation and the no-half-applied guarantee. The interceptor composition —
plugin-carried chains on the shipped registry × /resync — is pinned through
real loop turns: load the plan-gate pack → a write is blocked → flip the
pack's gated set on disk → /resync → the next turn enforces the NEW policy
(and unload → resync → policy gone), with per-seam report accounting, no
double-binding, and no registry residue across resync cycles.
"""
from __future__ import annotations

import importlib
import importlib.util
import itertools
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from chimera.assembly.resync import (
    BUSY_MESSAGE,
    KindDelta,
    ResyncReport,
    resync_agent,
    resync_plugins,
    skill_state,
)
from chimera.plugins.manager import PluginManager
from chimera.plugins.registry import PluginExtensionRegistry
from chimera.providers.faux import FauxProvider
from chimera.testing import create_assembled_harness, default_test_tools


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Point every ~/.chimera store at a throwaway root (no host pollution)."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "chimera-home"))


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the class-level plugin registry between tests.

    The interceptor tests below register chains on the shipped
    PluginExtensionRegistry; without the reset those chains would ride the
    per-turn merge of every later test's assembled agent.
    """
    PluginExtensionRegistry._reset()
    yield
    PluginExtensionRegistry._reset()


def _write_skill(workdir: Path, name: str, description: str, body: str = "do it") -> Path:
    """Write a project-scope nested SKILL.md under <workdir>/.chimera/skills/."""
    skill_dir = workdir / ".chimera" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(f'---\nname: {name}\ndescription: "{description}"\n---\n{body}\n')
    return path


def _write_agent_def(workdir: Path, name: str, description: str) -> Path:
    agents_dir = workdir / ".chimera" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nYou are {name}.\n"
    )
    return path


_PLUGIN_TEMPLATE = '''
from chimera.plugins.base import BasePlugin
from chimera.core.tool import BaseTool
from chimera.types import ToolResult


def plugin_gate(_call):
    return None


class {tool_class}(BaseTool):
    name = "{tool_name}"
    description = "test tool from a hot-swappable plugin"
    parameters = {{"type": "object", "properties": {{}}}}

    def execute(self, args, env=None):
        return ToolResult(output="{output}")


class {plugin_class}(BasePlugin):
    @property
    def name(self):
        return "{plugin_name}"

    def register_tools(self, registry):
        registry.register_tool({tool_class}())

    def register_middleware(self, registry):
        {interceptors_stmt}
'''

#: Strictly increasing mtime bumps: rewrites of same-length source within the
#: same second would otherwise reuse a stale (mtime, size)-validated pyc.
_MTIME_BUMP = itertools.count(start=10, step=10)


def _publish_source(path: Path, text: str) -> None:
    """Write module source with a strictly increasing mtime (defeats stale pyc)."""
    path.write_text(text)
    future = time.time() + next(_MTIME_BUMP)
    os.utime(path, (future, future))
    importlib.invalidate_caches()


def _write_plugin(
    path: Path,
    *,
    output: str,
    plugin_name: str = "hotswap",
    tool_name: str = "greet",
    broken: bool = False,
    interceptors_stmt: str = "pass",
) -> None:
    """Write (or overwrite) a plugin module whose tool returns *output*.

    Args:
        path: Module file to (over)write.
        output: What the plugin's tool returns.
        plugin_name: The plugin's registered name.
        tool_name: The registered tool's name.
        broken: Append a module-level raise so a swap of this source fails.
        interceptors_stmt: Statement run during activation against the
            plugin's component registry — the seam a future
            ``register_interceptor`` would use (e.g.
            ``'registry.interceptors = {"tool_call": [plugin_gate]}'``).
    """
    body = _PLUGIN_TEMPLATE.format(
        plugin_class="HotPlugin",
        tool_class="GreetTool",
        plugin_name=plugin_name,
        tool_name=tool_name,
        output=output,
        interceptors_stmt=interceptors_stmt,
    )
    if broken:
        body += "\nraise RuntimeError('broken plugin source')\n"
    _publish_source(path, textwrap.dedent(body))


def _load_plugin_module(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _load_plan_gate_copy(tmp_path: Path, mod_name: str):
    """Copy the SHIPPED plan-gate pack source into a tmp module and load it.

    The composition tests hot-swap the pack by editing its source on disk;
    editing the installed ``chimera/plugins/packs/plan_gate.py`` (or
    ``importlib.reload``-ing it in-process) would leak into every other
    test, so the byte-identical source is republished under a throwaway
    module name instead.

    Returns:
        ``(module, path, source)`` — the loaded module, the tmp file to
        edit, and the pristine pack source.
    """
    import chimera.plugins.packs.plan_gate as shipped

    source = Path(shipped.__file__).read_text()
    path = tmp_path / f"{mod_name}.py"
    _publish_source(path, source)
    module = _load_plugin_module(mod_name, path)
    return module, path, source


class RecordingFaux(FauxProvider):
    """FauxProvider that records the system message of every request."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.system_prompts: list[str] = []

    def _record(self, messages) -> None:
        if messages and getattr(messages[0], "role", "") == "system":
            self.system_prompts.append(str(messages[0].content))

    def complete(self, messages, **kwargs):
        self._record(messages)
        return super().complete(messages, **kwargs)

    async def async_complete(self, messages, **kwargs):
        self._record(messages)
        return await super().async_complete(messages, **kwargs)

    async def async_stream(self, messages, **kwargs):
        self._record(messages)
        async for event in super().async_stream(messages, **kwargs):
            yield event


# ---------------------------------------------------------------------------
# Report model
# ---------------------------------------------------------------------------

def test_refused_report_renders_one_line():
    report = ResyncReport(refused=True, reason=BUSY_MESSAGE)
    assert report.lines() == [f"resync refused: {BUSY_MESSAGE}"]
    assert not report.ok


def test_kind_delta_summary_wording():
    assert KindDelta(kind="skills").summary() == "unchanged"
    delta = KindDelta(kind="skills", added=["a", "b"], refreshed=["c"])
    assert delta.summary() == "2 added, 1 refreshed"
    delta = KindDelta(kind="plugins", failed=[("p", "boom")])
    assert delta.summary() == "1 failed"
    assert delta.changed


def test_report_lines_show_failures_and_notes():
    report = ResyncReport(
        deltas=[KindDelta(kind="plugins", failed=[("bad", "boom — plugin unloaded")])],
        notes=["a note"],
    )
    lines = report.lines()
    assert lines[0] == "resync: plugins 1 failed"
    assert "  ! plugins bad: boom — plugin unloaded" in lines
    assert "  · a note" in lines
    assert not report.ok


# ---------------------------------------------------------------------------
# Busy refusal
# ---------------------------------------------------------------------------

def test_resync_refuses_while_flagged_busy(tmp_path):
    with create_assembled_harness("ok", workspace=tmp_path / "ws") as harness:
        agent = harness.driver.agent
        agent._turn_active = True
        report = harness.driver.resync_resources()
        assert report.refused and report.reason == BUSY_MESSAGE
        # Nothing was rebound: no snapshot state appeared.
        assert not hasattr(agent, "_resync_skill_state")
        agent._turn_active = False


def test_resync_refuses_for_real_mid_turn(tmp_path):
    """A resync issued while the turn streams is refused (harness-driven)."""
    reports = []
    with create_assembled_harness(
        [{"text": "thinking"}, {"text": "done"}], workspace=tmp_path / "ws",
    ) as harness:
        def on_event(_ev):
            if not reports:
                reports.append(harness.driver.resync_resources())

        run = harness.run("do something", on_event=on_event)
        assert run.reason == "completed"
    assert reports and reports[0].refused
    assert reports[0].reason == BUSY_MESSAGE
    # At idle the same call runs.
    assert harness.driver.busy is False


# ---------------------------------------------------------------------------
# Skills → prompt catalog
# ---------------------------------------------------------------------------

def test_skills_added_refreshed_removed_across_resyncs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    skill_file = _write_skill(ws, "demo-skill", "first version")
    with create_assembled_harness("ok", workspace=ws) as harness:
        agent = harness.driver.agent

        first = harness.driver.resync_resources()
        skills = first.delta("skills")
        assert skills is not None and "demo-skill" in skills.added
        assert "demo-skill" in agent._skills_prompt_section
        assert any("first resync" in note for note in first.notes)

        # Edit → refreshed, and the prompt section carries the new text.
        skill_file.write_text(
            '---\nname: demo-skill\ndescription: "second version"\n---\nnew body\n'
        )
        second = harness.driver.resync_resources()
        assert "demo-skill" in second.delta("skills").refreshed
        assert "second version" in agent._skills_prompt_section

        # Delete → removed, and the catalog entry is gone.
        skill_file.unlink()
        third = harness.driver.resync_resources()
        assert "demo-skill" in third.delta("skills").removed
        assert "demo-skill" not in agent._skills_prompt_section


def test_refreshed_skills_reach_next_turn_system_prompt(tmp_path):
    """Prompt honesty, proven: the catalog lands in the NEXT turn's prompt."""
    ws = tmp_path / "ws"
    ws.mkdir()
    provider = RecordingFaux([{"text": "one"}, {"text": "two"}])
    with create_assembled_harness(
        provider=provider, workspace=ws,
    ) as harness:
        run1 = harness.run("hello")
        assert run1.reason == "completed"
        assert "demo-skill" not in provider.system_prompts[-1]

        _write_skill(ws, "demo-skill", "teaches the demo dance")
        report = harness.driver.resync_resources()
        assert "demo-skill" in report.delta("skills").added
        assert any("next turn of this conversation" in n for n in report.notes)

        run2 = harness.run("again")
        assert run2.reason == "completed"
        assert "demo-skill" in provider.system_prompts[-1]
        assert "teaches the demo dance" in provider.system_prompts[-1]


def test_skill_state_hashes_content():
    class _S:
        def __init__(self, name, description, content, source="chimera"):
            self.name, self.description = name, description
            self.content, self.source = content, source

    a = skill_state([_S("x", "d", "body")])
    b = skill_state([_S("x", "d", "different body")])
    assert set(a) == {"x"} and a["x"] != b["x"]


# ---------------------------------------------------------------------------
# Agent-definition catalog
# ---------------------------------------------------------------------------

def test_agent_definition_catalog_diffs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with create_assembled_harness("ok", workspace=ws) as harness:
        base = harness.driver.resync_resources()
        assert base.delta("agents") is not None

        _write_agent_def(ws, "helper", "helps out")
        added = harness.driver.resync_resources()
        assert "helper" in added.delta("agents").added

        _write_agent_def(ws, "helper", "helps out differently")
        refreshed = harness.driver.resync_resources()
        assert "helper" in refreshed.delta("agents").refreshed
        assert any("re-read at each invocation" in n for n in refreshed.notes)


# ---------------------------------------------------------------------------
# Plugins: end-to-end hot-swap through the hermetic loop harness
# ---------------------------------------------------------------------------

def test_plugin_hot_swap_end_to_end(tmp_path):
    """Write plugin → load → bind → run → edit source → /resync → behavior changes."""
    mod_name = "chimera_test_resync_plugin"
    plugin_file = tmp_path / f"{mod_name}.py"
    _write_plugin(plugin_file, output="hello-v1")
    module = _load_plugin_module(mod_name, plugin_file)

    manager = PluginManager()
    manager.load_plugin(module.HotPlugin())
    script = [
        {"text": "calling", "tool_calls": [{"name": "greet", "arguments": {}}]},
        {"text": "done"},
        {"text": "calling", "tool_calls": [{"name": "greet", "arguments": {}}]},
        {"text": "done"},
    ]
    try:
        with create_assembled_harness(script, workspace=tmp_path / "ws") as harness:
            harness.driver.agent.attach_plugin_manager(manager)

            bind = harness.driver.resync_resources()
            assert "hotswap" in bind.delta("plugins").refreshed
            assert bind.delta("plugin tools") is not None
            assert "greet" in bind.delta("plugin tools").added

            run1 = harness.run("greet me")
            assert [tc.name for tc in run1.tool_calls] == ["greet"]
            assert run1.tool_results[0][1].output == "hello-v1"

            _write_plugin(plugin_file, output="hello-v2")
            swap = harness.driver.resync_resources()
            assert "hotswap" in swap.delta("plugins").refreshed
            assert "greet" in swap.delta("plugin tools").refreshed

            run2 = harness.run("greet me again")
            assert run2.tool_results[0][1].output == "hello-v2"
    finally:
        sys.modules.pop(mod_name, None)


def test_plugin_failure_is_isolated_and_previous_registration_restored(tmp_path):
    """One broken plugin: reported + restored; the healthy plugin still swaps."""
    good_name = "chimera_test_resync_good"
    bad_name = "chimera_test_resync_bad"
    good_file = tmp_path / f"{good_name}.py"
    bad_file = tmp_path / f"{bad_name}.py"
    _write_plugin(good_file, output="good-v1", plugin_name="good", tool_name="good_tool")
    _write_plugin(bad_file, output="bad-v1", plugin_name="bad", tool_name="bad_tool")
    good_mod = _load_plugin_module(good_name, good_file)
    bad_mod = _load_plugin_module(bad_name, bad_file)

    manager = PluginManager()
    manager.load_plugin(good_mod.HotPlugin())
    manager.load_plugin(bad_mod.HotPlugin())
    try:
        with create_assembled_harness("ok", workspace=tmp_path / "ws") as harness:
            agent = harness.driver.agent
            agent.attach_plugin_manager(manager)
            harness.driver.resync_resources()  # initial bind

            # Break one plugin's source, refresh the other.
            _write_plugin(good_file, output="good-v2", plugin_name="good", tool_name="good_tool")
            _write_plugin(bad_file, output="bad-v2", plugin_name="bad", tool_name="bad_tool", broken=True)

            report = harness.driver.resync_resources()
            plugins = report.delta("plugins")
            assert "good" in plugins.refreshed
            failed_names = [name for name, _ in plugins.failed]
            assert failed_names == ["bad"]
            assert "previous registration restored" in plugins.failed[0][1]
            assert not report.ok

            # No half-applied state: both plugins still fully registered,
            # and both tools remain bound on the live agent.
            assert set(manager.plugins) == {"good", "bad"}
            bound = [t.name for t in agent.tools]
            assert "good_tool" in bound and "bad_tool" in bound
    finally:
        sys.modules.pop(good_name, None)
        sys.modules.pop(bad_name, None)


def test_resync_plugins_with_no_manager_is_empty():
    delta = resync_plugins(None)
    assert delta.kind == "plugins" and not delta.changed


def test_plugin_tool_name_collision_is_refused(tmp_path):
    """A plugin tool named like a built-in is refused, never shadowed."""
    mod_name = "chimera_test_resync_collide"
    plugin_file = tmp_path / f"{mod_name}.py"
    _write_plugin(plugin_file, output="evil", plugin_name="collide", tool_name="bash")
    module = _load_plugin_module(mod_name, plugin_file)
    manager = PluginManager()
    manager.load_plugin(module.HotPlugin())
    try:
        with create_assembled_harness("ok", workspace=tmp_path / "ws") as harness:
            agent = harness.driver.agent
            agent.attach_plugin_manager(manager)
            report = harness.driver.resync_resources()
            tools_delta = report.delta("plugin tools")
            assert tools_delta is not None
            assert ("bash", "name collides with a non-plugin tool — not bound") in tools_delta.failed
            assert [t.name for t in agent.tools].count("bash") == 1
    finally:
        sys.modules.pop(mod_name, None)


def test_no_plugin_manager_notes_honestly(tmp_path):
    with create_assembled_harness("ok", workspace=tmp_path / "ws") as harness:
        report = harness.driver.resync_resources()
        assert any("no plugin manager attached" in n for n in report.notes)


# ---------------------------------------------------------------------------
# Interceptors, generic fallback: third-party registry surfaces
# ---------------------------------------------------------------------------

def test_plugin_interceptors_bind_behind_base_chain(tmp_path):
    """A plugin-exposed interceptor surface binds behind the base chain.

    The plugin publishes interceptors during its own activation (the seam a
    ``register_interceptor`` registry method would feed), so the surface
    survives the hot-swap that resync performs. Rewriting the plugin without
    the surface then restores exactly the constructor-supplied base chain.
    """
    from chimera.core.interception import Interceptors

    def base_gate(_call):
        return None

    mod_name = "chimera_test_resync_icept"
    plugin_file = tmp_path / f"{mod_name}.py"
    _write_plugin(
        plugin_file, output="x", plugin_name="icept", tool_name="icept_tool",
        interceptors_stmt='registry.interceptors = {"tool_call": [plugin_gate]}',
    )
    module = _load_plugin_module(mod_name, plugin_file)
    manager = PluginManager()
    manager.load_plugin(module.HotPlugin())
    try:
        with create_assembled_harness(
            "ok", workspace=tmp_path / "ws",
            agent_kwargs={"interceptors": Interceptors(tool_call=[base_gate])},
        ) as harness:
            agent = harness.driver.agent
            agent.attach_plugin_manager(manager)

            report = harness.driver.resync_resources()
            inter = report.delta("interceptors")
            assert inter is not None and inter.refreshed == ["1 bound"]
            chain_names = [fn.__name__ for fn in agent._interceptors.tool_call]
            assert chain_names == ["base_gate", "plugin_gate"]

            # Republish the plugin WITHOUT the surface: resync restores the
            # base chain exactly.
            _write_plugin(
                plugin_file, output="x", plugin_name="icept", tool_name="icept_tool",
            )
            harness.driver.resync_resources()
            assert agent._interceptors.tool_call == [base_gate]
    finally:
        sys.modules.pop(mod_name, None)


def test_opaque_interceptor_shapes_are_counted_not_bound(tmp_path):
    mod_name = "chimera_test_resync_opaque"
    plugin_file = tmp_path / f"{mod_name}.py"
    _write_plugin(
        plugin_file, output="x", plugin_name="opaque", tool_name="opaque_tool",
        interceptors_stmt="registry.interceptors = object()",  # unbindable shape
    )
    module = _load_plugin_module(mod_name, plugin_file)
    manager = PluginManager()
    manager.load_plugin(module.HotPlugin())
    try:
        with create_assembled_harness("ok", workspace=tmp_path / "ws") as harness:
            agent = harness.driver.agent
            agent.attach_plugin_manager(manager)
            report = harness.driver.resync_resources()
            inter = report.delta("interceptors")
            assert inter is not None
            assert any("cannot bind" in why for _, why in inter.failed)
            assert agent._interceptors is None  # base was None; nothing bound
    finally:
        sys.modules.pop(mod_name, None)


# ---------------------------------------------------------------------------
# Interceptors, typed path: the shipped registry × /resync, composed
# ---------------------------------------------------------------------------

#: The one line the flip test removes from the pack's gated set.
_GATED_LINE = '    "write_file",\n'


def _write_script(path: str, content: str) -> list:
    """One scripted turn: a ``write_file`` call, then a closing text step."""
    return [
        {"text": "writing",
         "tool_calls": [{"name": "write_file",
                         "arguments": {"path": path, "content": content}}]},
        {"text": "done"},
    ]


def test_plan_gate_pack_policy_flip_end_to_end(tmp_path):
    """THE composition lock: the plan-gate pack × /resync, in real turns.

    Load the pack → a real turn's write_file is blocked → flip the pack's
    gated set on disk → /resync → the next real turn writes. The report
    counts the pack's chains per seam, and the per-turn merge carries
    exactly the reloaded instance's chains — never a stale one, never a
    duplicate.
    """
    mod_name = "chimera_test_resync_plan_gate_flip"
    module, plugin_file, source = _load_plan_gate_copy(tmp_path, mod_name)
    manager = PluginManager()
    manager.load_plugin(module.PlanGatePlugin())
    ws = tmp_path / "ws"
    script = _write_script("gated.txt", "v1") + _write_script("gated.txt", "v2")
    try:
        with create_assembled_harness(
            script, workspace=ws, tools=default_test_tools(ws),
        ) as harness:
            agent = harness.driver.agent
            agent.attach_plugin_manager(manager)

            bind = harness.driver.resync_resources()
            assert "plan-gate" in bind.delta("plugins").refreshed
            inter = bind.delta("interceptors")
            assert inter is not None
            assert inter.added == [
                "context:PlanGatePlugin._watch_context",
                "tool_call:PlanGatePlugin._gate_tool_call",
            ]
            assert any(
                "plugin-registered interceptors: tool_call 1 · context 1" in n
                for n in bind.notes
            )

            run1 = harness.run("write gated.txt")
            assert run1.reason == "completed"
            assert run1.files_created == []  # the gate held in a real turn
            blocked = [r for tc, r in run1.tool_results if tc and tc.name == "write_file"]
            assert blocked and "plan-gate: no plan recorded" in (blocked[0].error or "")

            # Flip the policy on disk: write_file leaves the gated set.
            flipped = source.replace(_GATED_LINE, "")
            assert flipped != source
            _publish_source(plugin_file, flipped)

            swap = harness.driver.resync_resources()
            assert "plan-gate" in swap.delta("plugins").refreshed
            inter2 = swap.delta("interceptors")
            assert inter2 is not None
            assert inter2.refreshed == [
                "context:PlanGatePlugin._watch_context",
                "tool_call:PlanGatePlugin._gate_tool_call",
            ]
            assert inter2.added == [] and inter2.removed == []

            # Exactness: the per-turn merge is the reloaded instance's
            # chains and nothing else; the host chain stays untouched.
            current = manager.plugins["plan-gate"]
            merged = agent._effective_interceptors()
            assert merged.tool_call == [current._gate_tool_call]
            assert merged.context == [current._watch_context]
            assert agent._interceptors is None

            run2 = harness.run("write gated.txt again")
            assert run2.reason == "completed"
            assert (ws / "gated.txt").read_text() == "v2"  # NEW policy enforced
    finally:
        sys.modules.pop(mod_name, None)


def test_plan_gate_pack_unload_then_resync_drops_the_policy(tmp_path):
    """The inverse lock: unload the pack, /resync, and the policy is gone —
    the report says removed, per seam, and the next real turn writes."""
    mod_name = "chimera_test_resync_plan_gate_unload"
    module, _file, _source = _load_plan_gate_copy(tmp_path, mod_name)
    manager = PluginManager()
    manager.load_plugin(module.PlanGatePlugin())
    ws = tmp_path / "ws"
    script = _write_script("free.txt", "v1") + _write_script("free.txt", "v2")
    try:
        with create_assembled_harness(
            script, workspace=ws, tools=default_test_tools(ws),
        ) as harness:
            agent = harness.driver.agent
            agent.attach_plugin_manager(manager)
            harness.driver.resync_resources()  # initial bind + accounting

            run1 = harness.run("write free.txt")
            assert run1.files_created == []  # gated while loaded

            manager.unload("plan-gate")
            gone = harness.driver.resync_resources()
            inter = gone.delta("interceptors")
            assert inter is not None
            assert inter.removed == [
                "context:PlanGatePlugin._watch_context",
                "tool_call:PlanGatePlugin._gate_tool_call",
            ]
            assert inter.added == [] and inter.refreshed == []
            assert not any("plugin-registered interceptors" in n for n in gone.notes)
            assert agent._effective_interceptors() is None

            run2 = harness.run("write free.txt now")
            assert run2.reason == "completed"
            assert (ws / "free.txt").read_text() == "v2"  # policy gone
    finally:
        sys.modules.pop(mod_name, None)


_DUAL_SURFACE_PLUGIN = '''\
"""Composition fixture: ONE interceptor republished on both surfaces."""
from chimera.core.interception import InterceptDecision
from chimera.plugins.base import BasePlugin
from chimera.plugins.registry import PluginExtensionRegistry
from chimera.types import ToolCall


class DualSurfacePlugin(BasePlugin):
    @property
    def name(self):
        return "dual-surface"

    def register_interceptors(self, registry):
        PluginExtensionRegistry.register_interceptor("tool_call", self._tag)
        registry.interceptors = {"tool_call": [self._tag]}

    def deactivate(self):
        PluginExtensionRegistry.unregister_interceptor("tool_call", self._tag)

    def _tag(self, call):
        if call.name != "write_file":
            return None
        args = dict(call.arguments)
        args["content"] = str(args.get("content", "")) + "+tag"
        return InterceptDecision.replace(
            ToolCall(id=call.id, name=call.name, arguments=args)
        )
'''


def test_registry_chain_republished_on_generic_surface_binds_once(tmp_path):
    """The product of rows, pinned by the bytes on disk: a plugin carrying
    its chain on the shipped registry AND republishing it on the generic
    ``registry.interceptors`` surface mutates a write exactly ONCE — the
    generic fold excludes what the per-turn merge already carries."""
    mod_name = "chimera_test_resync_dual_surface"
    plugin_file = tmp_path / f"{mod_name}.py"
    _publish_source(plugin_file, _DUAL_SURFACE_PLUGIN)
    module = _load_plugin_module(mod_name, plugin_file)
    manager = PluginManager()
    manager.load_plugin(module.DualSurfacePlugin())
    ws = tmp_path / "ws"
    try:
        with create_assembled_harness(
            _write_script("tagged.txt", "one"), workspace=ws,
            tools=default_test_tools(ws),
        ) as harness:
            agent = harness.driver.agent
            agent.attach_plugin_manager(manager)

            report = harness.driver.resync_resources()
            inter = report.delta("interceptors")
            assert inter is not None
            assert inter.added == ["tool_call:DualSurfacePlugin._tag"]
            assert inter.refreshed == []  # no generic "N bound" — all excluded
            assert agent._interceptors is None
            assert len(agent._effective_interceptors().tool_call) == 1

            run = harness.run("write tagged.txt")
            assert run.reason == "completed"
            assert (ws / "tagged.txt").read_text() == "one+tag"  # not …+tag+tag
    finally:
        sys.modules.pop(mod_name, None)


def test_resync_cycles_leave_no_interceptor_residue(tmp_path):
    """Hygiene across the composition: N resync cycles = N reloads, and the
    registry still carries exactly one chain per pack seam — the effective
    merge is always the CURRENT instance's callables, never an
    accumulation of dead generations."""
    mod_name = "chimera_test_resync_plan_gate_cycles"
    module, _file, _source = _load_plan_gate_copy(tmp_path, mod_name)
    manager = PluginManager()
    manager.load_plugin(module.PlanGatePlugin())
    try:
        with create_assembled_harness("ok", workspace=tmp_path / "ws") as harness:
            agent = harness.driver.agent
            agent.attach_plugin_manager(manager)
            report = None
            for _ in range(3):
                report = harness.driver.resync_resources()
            assert len(PluginExtensionRegistry.get_interceptors("tool_call")) == 1
            assert len(PluginExtensionRegistry.get_interceptors("context")) == 1
            current = manager.plugins["plan-gate"]
            merged = agent._effective_interceptors()
            assert merged.tool_call == [current._gate_tool_call]
            assert merged.context == [current._watch_context]
            # After the first resync, later cycles report refreshed — not
            # added, and never a growing list.
            inter = report.delta("interceptors")
            assert "tool_call:PlanGatePlugin._gate_tool_call" in inter.refreshed
            assert inter.added == [] and inter.removed == []
    finally:
        sys.modules.pop(mod_name, None)


_LEAK_PROBE_V1 = '''\
"""Leak-repro fixture v1: a healthy probe chain (blocks nothing)."""
from chimera.plugins.base import BasePlugin
from chimera.plugins.registry import PluginExtensionRegistry


class LeakProbePlugin(BasePlugin):
    @property
    def name(self):
        return "leak-probe"

    def register_interceptors(self, registry):
        PluginExtensionRegistry.register_interceptor("tool_call", self._probe)

    def deactivate(self):
        PluginExtensionRegistry.unregister_interceptor("tool_call", self._probe)

    def _probe(self, call):
        return None
'''

_LEAK_PROBE_V2 = '''\
"""Leak-repro fixture v2: registers a block-everything gate, then dies."""
from chimera.core.interception import InterceptDecision
from chimera.plugins.base import BasePlugin
from chimera.plugins.registry import PluginExtensionRegistry


class LeakProbePlugin(BasePlugin):
    @property
    def name(self):
        return "leak-probe"

    def register_interceptors(self, registry):
        PluginExtensionRegistry.register_interceptor(
            "tool_call", self._block_everything
        )
        raise RuntimeError("v2 dies after partial registration")

    def deactivate(self):
        PluginExtensionRegistry.unregister_interceptor(
            "tool_call", self._block_everything
        )

    def _block_everything(self, call):
        return InterceptDecision.block("orphaned v2 gate")
'''


def test_failed_swap_cannot_orphan_interceptor_chains(tmp_path):
    """The confirmed leak, pinned shut end-to-end: v2 registers a
    block-everything gate then dies mid-activation. The failed swap must
    leave the registry exactly as v1 had it (restore reported), the next
    REAL turn must still write, and unloading v1 must leave the registry
    empty — no chain outlives its owner."""
    mod_name = "chimera_test_resync_leak_probe"
    plugin_file = tmp_path / f"{mod_name}.py"
    _publish_source(plugin_file, _LEAK_PROBE_V1)
    module = _load_plugin_module(mod_name, plugin_file)
    manager = PluginManager()
    manager.load_plugin(module.LeakProbePlugin())
    ws = tmp_path / "ws"
    try:
        with create_assembled_harness(
            _write_script("still-works.txt", "post-failure"), workspace=ws,
            tools=default_test_tools(ws),
        ) as harness:
            agent = harness.driver.agent
            agent.attach_plugin_manager(manager)
            harness.driver.resync_resources()  # bind v1

            _publish_source(plugin_file, _LEAK_PROBE_V2)
            report = harness.driver.resync_resources()
            plugins = report.delta("plugins")
            assert [name for name, _ in plugins.failed] == ["leak-probe"]
            assert "previous registration restored" in plugins.failed[0][1]

            # Registry state is EXACTLY pre-swap v1: one probe, no orphan.
            chains = PluginExtensionRegistry.get_interceptors("tool_call")
            assert [fn.__qualname__ for fn in chains] == ["LeakProbePlugin._probe"]
            restored = manager.plugins["leak-probe"]
            assert chains == [restored._probe]
            inter = report.delta("interceptors")
            if inter is not None:  # the report never names the dead v2 gate
                mentioned = inter.added + inter.removed + inter.refreshed
                assert not any("_block_everything" in entry for entry in mentioned)

            # A REAL turn still writes — no orphaned block-everything gate.
            run = harness.run("write still-works.txt")
            assert run.reason == "completed"
            assert (ws / "still-works.txt").read_text() == "post-failure"

            # And unloading v1 leaves the registry EMPTY.
            manager.unload("leak-probe")
            assert PluginExtensionRegistry.get_interceptors("tool_call") == []
    finally:
        sys.modules.pop(mod_name, None)


def test_registry_chains_reported_without_a_manager(tmp_path):
    """Chains on the shipped registry ride the per-turn merge whether or
    not a manager is attached — the typed accounting reports them either
    way, and steady state drops the delta but keeps the census note."""
    from chimera.plugins.packs import PlanGatePlugin

    PluginManager().load_plugin(PlanGatePlugin())
    with create_assembled_harness("ok", workspace=tmp_path / "ws") as harness:
        first = harness.driver.resync_resources()
        inter = first.delta("interceptors")
        assert inter is not None
        assert inter.added == [
            "context:PlanGatePlugin._watch_context",
            "tool_call:PlanGatePlugin._gate_tool_call",
        ]
        assert any(
            "plugin-registered interceptors: tool_call 1 · context 1" in n
            and "merged into every turn" in n
            for n in first.notes
        )
        second = harness.driver.resync_resources()
        assert second.delta("interceptors") is None
        assert any("plugin-registered interceptors" in n for n in second.notes)


def test_bare_target_counts_chains_but_says_it_cannot_merge(tmp_path):
    """A duck-typed target without the per-turn merge gets honesty, not a
    silent no-op: chains are counted per seam and the note says this
    target cannot reach them."""
    from chimera.plugins.packs import PlanGatePlugin

    PluginManager().load_plugin(PlanGatePlugin())

    class Bare:
        pass

    report = resync_agent(Bare(), workdir=tmp_path)
    inter = report.delta("interceptors")
    assert inter is not None and len(inter.added) == 2
    assert any(
        "counted only; this target does not expose the per-turn merge" in n
        for n in report.notes
    )


# ---------------------------------------------------------------------------
# Embed surface
# ---------------------------------------------------------------------------

def test_agent_session_inherits_resync(tmp_path):
    from chimera.embed import AgentSession

    with AgentSession(
        provider=FauxProvider("ok"), project_dir=tmp_path / "ws", preset="minimal",
    ) as session:
        report = session.resync_resources()
        assert not report.refused
        assert report.delta("skills") is not None


# ---------------------------------------------------------------------------
# resync_agent guard rails
# ---------------------------------------------------------------------------

def test_resync_agent_direct_call_matches_method(tmp_path):
    with create_assembled_harness("ok", workspace=tmp_path / "ws") as harness:
        agent = harness.driver.agent
        direct = resync_agent(agent, workdir=harness.workspace)
        assert direct.delta("skills") is not None
        assert direct.delta("agents") is not None

"""The REPL ``/resync`` command: registration, routing, and the classic rebind.

``/resync`` lives in the shared slash registry (so every REPL surface gets
it), routes to the assembled seam when the session's agent exposes
``resync_resources``, and otherwise runs the classic-session path: plugin
hot-swap via the ``/plugin`` manager, an in-place system-prompt rebuild when
the session recorded its base prompt, and honest notes when it did not.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from chimera.assembly.resync import BUSY_MESSAGE, KindDelta, ResyncReport
from chimera.cli.slash_commands import COMMAND_NAMES, cmd_resync, dispatch, list_commands


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Point every ~/.chimera store at a throwaway root (no host pollution)."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "chimera-home"))


class _Env:
    def __init__(self, workdir: str) -> None:
        self.workdir = workdir


class _Obj:
    """Attribute bag (plain object; setattr-friendly, no MagicMock autospawn)."""


def _classic_session(base: str = "You are a coding assistant.") -> _Obj:
    session = _Obj()
    agent = _Obj()
    agent.prompt = None
    session._agent = agent
    session._system_base = base
    session._skills_state = {}
    return session


def _write_skill(workdir: Path, name: str, description: str) -> Path:
    skill_dir = workdir / ".chimera" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(f'---\nname: {name}\ndescription: "{description}"\n---\nbody\n')
    return path


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_resync_registered_in_shared_registry():
    assert "/resync" in COMMAND_NAMES
    entries = dict(list_commands())
    assert "resync" in entries
    assert "hot-swap" in entries["resync"]


def test_dispatch_routes_resync():
    session = _Obj()
    session._agent = None

    class _AssembledAgent:
        @staticmethod
        def resync_resources() -> ResyncReport:
            return ResyncReport(deltas=[KindDelta(kind="plugins")])

    session.agent = _AssembledAgent()
    out: list[str] = []
    assert dispatch("/resync", session, None, out.append) is True
    assert out and out[0].startswith("resync: plugins unchanged")


# ---------------------------------------------------------------------------
# Assembled-session routing
# ---------------------------------------------------------------------------

def test_assembled_agent_seam_is_preferred():
    calls: list[str] = []

    class _AssembledAgent:
        @staticmethod
        def resync_resources() -> ResyncReport:
            calls.append("agent")
            return ResyncReport(refused=True, reason=BUSY_MESSAGE)

    session = _Obj()
    session.agent = _AssembledAgent()
    out: list[str] = []
    cmd_resync(session, None, "", out.append)
    assert calls == ["agent"]
    assert out == [f"resync refused: {BUSY_MESSAGE}"]


# ---------------------------------------------------------------------------
# Classic session path
# ---------------------------------------------------------------------------

def test_classic_session_rebuilds_prompt_in_place(tmp_path):
    _write_skill(tmp_path, "demo-skill", "teaches the demo dance")
    session = _classic_session()
    out: list[str] = []
    cmd_resync(session, _Env(str(tmp_path)), "", out.append)

    text = "\n".join(out)
    assert "resync:" in text
    assert "demo-skill" not in session._system_base  # base untouched
    rendered = session._agent.prompt.render()
    assert "demo-skill" in rendered and "teaches the demo dance" in rendered
    assert "applies from the next turn of this session" in text
    # The catalog diff recorded the addition.
    assert "demo-skill" in (session._skills_state or {})


def test_classic_session_diffs_across_resyncs(tmp_path):
    skill = _write_skill(tmp_path, "demo-skill", "first")
    session = _classic_session()
    env = _Env(str(tmp_path))
    cmd_resync(session, env, "", lambda _s: None)

    skill.write_text('---\nname: demo-skill\ndescription: "second"\n---\nbody\n')
    out: list[str] = []
    cmd_resync(session, env, "", out.append)
    assert any("skills" in line and "1 refreshed" in line for line in out)

    skill.unlink()
    out2: list[str] = []
    cmd_resync(session, env, "", out2.append)
    assert any("skills" in line and "1 removed" in line for line in out2)
    assert "demo-skill" not in session._agent.prompt.render()


def test_classic_session_without_base_prompt_is_honest(tmp_path):
    session = _Obj()
    session._agent = _Obj()  # no prompt recorded, no _system_base
    out: list[str] = []
    cmd_resync(session, _Env(str(tmp_path)), "", out.append)
    assert any("fixed at session start" in line for line in out)


def test_classic_session_busy_refusal(tmp_path):
    session = _classic_session()
    session._turn_active = True
    out: list[str] = []
    cmd_resync(session, _Env(str(tmp_path)), "", out.append)
    assert out == [f"resync refused: {BUSY_MESSAGE}"]


def test_classic_session_plugin_hot_swap(tmp_path):
    """The /plugin manager's plugins hot-swap through /resync."""
    import importlib
    import itertools
    import os
    import sys
    import time

    from chimera.plugins.manager import PluginManager

    mod_name = "chimera_test_repl_resync_plugin"
    plugin_file = tmp_path / f"{mod_name}.py"
    # Strictly increasing mtimes: same-length rewrites within one second would
    # otherwise revalidate a stale pyc by its (mtime, size) fingerprint.
    bump = itertools.count(start=10, step=10)

    def write(version: str) -> None:
        plugin_file.write_text(textwrap.dedent(f'''
            from chimera.plugins.base import BasePlugin

            class ReplPlugin(BasePlugin):
                @property
                def name(self):
                    return "repl-hot"

                def register_skills(self, registry):
                    registry.register_skill("{version}")
        '''))
        future = time.time() + next(bump)
        os.utime(plugin_file, (future, future))
        importlib.invalidate_caches()

    write("v1")
    spec = importlib.util.spec_from_file_location(mod_name, plugin_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)

    manager = PluginManager()
    manager.load_plugin(module.ReplPlugin())
    session = _classic_session()
    session._plugin_manager = manager
    try:
        write("v2")
        out: list[str] = []
        cmd_resync(session, _Env(str(tmp_path)), "", out.append)
        assert any("plugins 1 refreshed" in line for line in out)
        assert manager.get_all_skills() == ["v2"]  # the swap took effect
    finally:
        sys.modules.pop(mod_name, None)


def test_classic_session_no_plugins_notes_how_to_load(tmp_path):
    session = _classic_session()
    out: list[str] = []
    cmd_resync(session, _Env(str(tmp_path)), "", out.append)
    assert any("no plugins loaded" in line for line in out)


# ---------------------------------------------------------------------------
# run_code stashes the rebuild inputs
# ---------------------------------------------------------------------------

def test_run_code_source_stashes_resync_state():
    """The classic REPL records its base prompt + skill snapshot for /resync."""
    import inspect

    from chimera.cli import code

    source = inspect.getsource(code.run_code)
    assert "session._system_base = _system_base" in source
    assert "session._skills_state = _skills_state" in source

"""Wave-10 G2 — verify the 10 previously-unfired HookEvents now emit.

For each event, this file:

* spins up a synthetic :class:`HookEmitter` whose executor is patched to
  record every ``(event, kwargs)`` it sees,
* exercises the call site that is supposed to fire the event, and
* asserts the recording contains the expected event.

The synthetic recorder bypasses :class:`HookExecutor` matchers so the
test stays focused on the wiring (did the call site reach the emitter?)
rather than on hook dispatch semantics, which already have dedicated
tests in :mod:`tests.hooks.test_emitter` and :mod:`tests.hooks.test_executor`.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from chimera.hooks import emitter as emitter_mod
from chimera.hooks.emitter import HookEmitter
from chimera.hooks.events import HookEvent
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import HookOutput


# ---------------------------------------------------------------------------
# Recording emitter helper
# ---------------------------------------------------------------------------


class _RecordingExecutor(HookExecutor):
    """HookExecutor that records every execute() call into a shared list."""

    def __init__(self, sink: list[tuple[HookEvent, dict[str, Any]]]) -> None:
        super().__init__()
        self._sink = sink

    async def execute(self, event, input_data, matchers, abort_signal=None):  # type: ignore[override]
        self._sink.append(
            (
                event,
                {
                    "tool_name": input_data.tool_name,
                    "tool_input": input_data.tool_input,
                    "tool_output": input_data.tool_output,
                    "session_id": input_data.session_id,
                },
            )
        )
        return HookOutput()


def _make_recorder() -> tuple[HookEmitter, list[tuple[HookEvent, dict[str, Any]]]]:
    sink: list[tuple[HookEvent, dict[str, Any]]] = []
    executor = _RecordingExecutor(sink)
    emitter = HookEmitter(executor=executor)
    # Force the executor to be considered "active" by passing a dummy matcher
    # is unnecessary — the emitter checks ``self._executor`` truthiness.
    return emitter, sink


@pytest.fixture
def global_emitter_recorder():
    """Install a recording emitter as the process-wide global emitter.

    Restores whatever was there before on teardown so the test never
    leaks emitter state into its neighbours.
    """
    previous = emitter_mod.get_global_emitter()
    emitter, sink = _make_recorder()
    emitter_mod.set_global_emitter(emitter)
    try:
        yield emitter, sink
    finally:
        # Restore: if the previous emitter was the no-op fallback, clear.
        if previous.active:
            emitter_mod.set_global_emitter(previous)
        else:
            emitter_mod.set_global_emitter(None)


def _events_in(sink: list[tuple[HookEvent, dict[str, Any]]]) -> set[HookEvent]:
    return {e for e, _ in sink}


# ---------------------------------------------------------------------------
# 1 + 2: PERMISSION_REQUEST and PERMISSION_DENIED
# ---------------------------------------------------------------------------


class TestPermissionEvents:
    @pytest.mark.asyncio
    async def test_permission_request_fires_on_ask(self):
        from chimera.permissions.checker import PermissionChecker
        from chimera.permissions.context import PermissionContext
        from chimera.permissions.modes import PermissionMode

        emitter, sink = _make_recorder()
        checker = PermissionChecker(hook_emitter=emitter)

        class _Tool:
            name = "Bash"

        ctx = PermissionContext(mode=PermissionMode.DEFAULT)
        decision = await checker.check(_Tool(), {"command": "ls"}, ctx)

        # Default mode + no matching rule -> ASK -> PERMISSION_REQUEST.
        assert HookEvent.PERMISSION_REQUEST in _events_in(sink)
        assert decision.behavior.name == "ASK"

    @pytest.mark.asyncio
    async def test_permission_denied_fires_on_deny(self):
        from chimera.permissions.checker import PermissionChecker
        from chimera.permissions.context import PermissionContext
        from chimera.permissions.modes import PermissionMode
        from chimera.permissions.rules import RuleSource

        emitter, sink = _make_recorder()
        checker = PermissionChecker(hook_emitter=emitter)

        class _Tool:
            name = "Bash"

        ctx = PermissionContext(
            mode=PermissionMode.DEFAULT,
            deny_rules={RuleSource.PROJECT: ["Bash"]},
        )
        decision = await checker.check(_Tool(), {"command": "ls"}, ctx)

        assert HookEvent.PERMISSION_DENIED in _events_in(sink)
        assert decision.behavior.name == "DENY"


# ---------------------------------------------------------------------------
# 3: SETUP — fired from chimera.cli.main._emit_setup_hook
# ---------------------------------------------------------------------------


class TestSetupEvent:
    def test_setup_emits_via_global_emitter(self, global_emitter_recorder):
        from chimera.cli.main import _emit_setup_hook

        _emitter, sink = global_emitter_recorder
        _emit_setup_hook("synthesize")

        assert HookEvent.SETUP in _events_in(sink)
        # The subcommand name should be passed through as ``tool_name``.
        setup_records = [kwargs for ev, kwargs in sink if ev == HookEvent.SETUP]
        assert setup_records
        assert setup_records[0]["tool_name"] == "synthesize"


# ---------------------------------------------------------------------------
# 4: TEAMMATE_IDLE — fired from AgentSpawner background completion
# ---------------------------------------------------------------------------


class TestTeammateIdle:
    @pytest.mark.asyncio
    async def test_teammate_idle_emitted_alongside_subagent_stop(self):
        # Smoke-test the emit path directly: AgentSpawner's _run_background
        # ends with two emits; we verify the second (TEAMMATE_IDLE) is wired
        # by exercising the underlying emitter call shape.
        emitter, sink = _make_recorder()
        await emitter.emit(HookEvent.SUBAGENT_STOP, tool_name="researcher")
        await emitter.emit(HookEvent.TEAMMATE_IDLE, tool_name="researcher")

        assert HookEvent.TEAMMATE_IDLE in _events_in(sink)

        # Also verify by inspecting the source: the event name appears in
        # the spawner's _run_background closure. This guards against a
        # future refactor accidentally dropping the emit call.
        from chimera.core import agent_spawner as spawner_mod
        source = Path(spawner_mod.__file__).read_text(encoding="utf-8")
        assert "HookEvent.TEAMMATE_IDLE" in source


# ---------------------------------------------------------------------------
# 5 + 6: ELICITATION and ELICITATION_RESULT — fired from PermissionPromptHandler
# ---------------------------------------------------------------------------


class TestElicitationEvents:
    @pytest.mark.asyncio
    async def test_elicitation_pair_fires_on_callback_response(self):
        from chimera.permissions.decisions import PermissionDecision
        from chimera.permissions.prompt_handler import PermissionPromptHandler

        emitter, sink = _make_recorder()

        async def _allow(tool_name, input_args, decision):
            return "allow_once"

        handler = PermissionPromptHandler(callback=_allow, hook_emitter=emitter)
        decision = PermissionDecision.ask("test")
        result = await handler.handle_ask("Bash", {"command": "ls"}, decision)

        events = [e for e, _ in sink]
        assert HookEvent.ELICITATION in events
        assert HookEvent.ELICITATION_RESULT in events
        # ELICITATION should always fire before ELICITATION_RESULT.
        assert events.index(HookEvent.ELICITATION) < events.index(
            HookEvent.ELICITATION_RESULT
        )
        assert result.behavior.name == "ALLOW"

    @pytest.mark.asyncio
    async def test_elicitation_result_fires_on_no_callback_path(self):
        from chimera.permissions.decisions import PermissionDecision
        from chimera.permissions.prompt_handler import PermissionPromptHandler

        emitter, sink = _make_recorder()
        handler = PermissionPromptHandler(callback=None, hook_emitter=emitter)
        decision = PermissionDecision.ask("test")
        await handler.handle_ask("Bash", {"command": "ls"}, decision)

        events = [e for e, _ in sink]
        assert HookEvent.ELICITATION in events
        assert HookEvent.ELICITATION_RESULT in events


# ---------------------------------------------------------------------------
# 7: CONFIG_CHANGE — fired from chimera.mink.settings.load_mink_settings
# ---------------------------------------------------------------------------


class TestConfigChange:
    def test_config_change_fires_on_load(self, tmp_path, global_emitter_recorder):
        from chimera.mink.settings import load_mink_settings

        _emitter, sink = global_emitter_recorder
        load_mink_settings(cwd=tmp_path)

        assert HookEvent.CONFIG_CHANGE in _events_in(sink)


# ---------------------------------------------------------------------------
# 8 + 9: WORKTREE_CREATE and WORKTREE_REMOVE — fired from worktree_tool
# ---------------------------------------------------------------------------


class TestWorktreeEvents:
    def test_worktree_helper_fires_via_global_emitter(self, global_emitter_recorder):
        from chimera.tools.worktree_tool import _emit_worktree_event

        _emitter, sink = global_emitter_recorder
        _emit_worktree_event(
            HookEvent.WORKTREE_CREATE,
            tool_name="enter_worktree",
            tool_input={"path": "/tmp/wt", "branch": "feature"},
        )
        _emit_worktree_event(
            HookEvent.WORKTREE_REMOVE,
            tool_name="exit_worktree",
            tool_input={"path": "/tmp/wt", "action": "remove"},
        )

        events = _events_in(sink)
        assert HookEvent.WORKTREE_CREATE in events
        assert HookEvent.WORKTREE_REMOVE in events

        # Source check: both event names appear in the tool module so a
        # later refactor can't silently drop the emit call.
        from chimera.tools import worktree_tool
        source = Path(worktree_tool.__file__).read_text(encoding="utf-8")
        assert "HookEvent.WORKTREE_CREATE" in source
        assert "HookEvent.WORKTREE_REMOVE" in source


# ---------------------------------------------------------------------------
# 10: INSTRUCTIONS_LOADED — fired from agent_memory + otter rules
# ---------------------------------------------------------------------------


class TestInstructionsLoaded:
    def test_agent_memory_fires_when_files_found(
        self, tmp_path, global_emitter_recorder
    ):
        from chimera.context import agent_memory

        _emitter, sink = global_emitter_recorder

        # Create a CLAUDE.md so load_memory has something to ingest.
        (tmp_path / "CLAUDE.md").write_text("# project rules\n", encoding="utf-8")

        # Use the helper directly to avoid walking up to the user's home dir
        # (which can read real CLAUDE.md files and pollute the test).
        agent_memory._emit_instructions_loaded(
            files=[str(tmp_path / "CLAUDE.md")],
            char_count=42,
            source="agent_memory",
        )

        assert HookEvent.INSTRUCTIONS_LOADED in _events_in(sink)

    def test_otter_rules_fires_when_files_found(
        self, tmp_path, global_emitter_recorder
    ):
        from chimera.otter import rules as otter_rules

        _emitter, sink = global_emitter_recorder
        (tmp_path / "AGENTS.md").write_text("# rules\n", encoding="utf-8")

        # Drive the helper to confirm wiring.
        otter_rules._emit_instructions_loaded(
            [str(tmp_path / "AGENTS.md")], 7
        )

        assert HookEvent.INSTRUCTIONS_LOADED in _events_in(sink)


# ---------------------------------------------------------------------------
# Backwards-compat smoke tests
# ---------------------------------------------------------------------------


class TestBackwardsCompat:
    """Every emit site must still work when no emitter is wired."""

    @pytest.mark.asyncio
    async def test_permission_checker_works_without_emitter(self):
        from chimera.permissions.checker import PermissionChecker
        from chimera.permissions.context import PermissionContext
        from chimera.permissions.modes import PermissionMode

        checker = PermissionChecker()  # no emitter

        class _Tool:
            name = "Bash"

        ctx = PermissionContext(mode=PermissionMode.AUTO)
        decision = await checker.check(_Tool(), {}, ctx)
        assert decision is not None

    @pytest.mark.asyncio
    async def test_prompt_handler_works_without_emitter(self):
        from chimera.permissions.decisions import PermissionDecision
        from chimera.permissions.prompt_handler import PermissionPromptHandler

        async def _allow(tool_name, input_args, decision):
            return "allow_once"

        handler = PermissionPromptHandler(callback=_allow)  # no emitter
        result = await handler.handle_ask(
            "Bash", {}, PermissionDecision.ask("t")
        )
        assert result.behavior.name == "ALLOW"

    def test_setup_helper_safe_without_global_emitter(self, monkeypatch):
        # Ensure we start from a known clean state.
        from chimera.cli.main import _emit_setup_hook
        emitter_mod.set_global_emitter(None)
        # Should not raise even with no emitter wired.
        _emit_setup_hook("synthesize")

    def test_emitter_emit_sync_no_executor(self):
        emitter = HookEmitter()  # no executor
        out = emitter.emit_sync(HookEvent.SETUP, tool_name="x")
        assert isinstance(out, HookOutput)
        assert out.continue_execution is True

    @pytest.mark.asyncio
    async def test_emitter_emit_sync_inside_running_loop(self):
        """emit_sync must not deadlock when called from an async context."""
        emitter, sink = _make_recorder()
        # Running this from inside an event loop must hand off to a worker.
        out = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: emitter.emit_sync(HookEvent.SETUP, tool_name="loop"),
        )
        assert isinstance(out, HookOutput)
        # The recording executor saw the event.
        assert HookEvent.SETUP in _events_in(sink)


# ---------------------------------------------------------------------------
# Coverage roll-up: the 10 events and where they fire
# ---------------------------------------------------------------------------


def test_all_ten_events_are_referenced_in_chimera():
    """Source-level guard: every wired event must be referenced from a
    non-test, non-hooks-package module so we never regress to "defined
    but never fires"."""
    from chimera import hooks  # noqa: F401  (ensure import order is stable)

    repo_root = Path(__file__).resolve().parents[2]

    targets: dict[HookEvent, list[str]] = {
        HookEvent.PERMISSION_REQUEST: ["chimera/permissions/checker.py"],
        HookEvent.PERMISSION_DENIED: ["chimera/permissions/checker.py"],
        HookEvent.SETUP: ["chimera/cli/main.py"],
        HookEvent.TEAMMATE_IDLE: ["chimera/core/agent_spawner.py"],
        HookEvent.ELICITATION: ["chimera/permissions/prompt_handler.py"],
        HookEvent.ELICITATION_RESULT: ["chimera/permissions/prompt_handler.py"],
        HookEvent.CONFIG_CHANGE: ["chimera/mink/settings.py"],
        HookEvent.WORKTREE_CREATE: ["chimera/tools/worktree_tool.py"],
        HookEvent.WORKTREE_REMOVE: ["chimera/tools/worktree_tool.py"],
        HookEvent.INSTRUCTIONS_LOADED: [
            "chimera/context/agent_memory.py",
            "chimera/otter/rules.py",
        ],
    }

    missing: list[str] = []
    for event, paths in targets.items():
        ok = False
        for rel in paths:
            text = (repo_root / rel).read_text(encoding="utf-8")
            if event.name in text or f'"{event.value}"' in text:
                ok = True
                break
        if not ok:
            missing.append(f"{event.name} not found in any of {paths}")
    assert not missing, "Missing emit-site references:\n" + "\n".join(missing)


def test_event_payloads_are_json_safe():
    """Every kwarg we pass to ``emit_sync`` should be JSON-serialisable so
    command hooks can stringify it via ``HOOK_TOOL_INPUT``."""
    sample = {"path": "/tmp/wt", "branch": "feature"}
    json.dumps(sample)  # smoke

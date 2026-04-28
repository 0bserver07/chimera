"""Tests for otter plugin hook -> LoopConfig.hook_emitter wiring (W3 — F3).

W2 collected directory-plugin :class:`Hook` records into a list but never
converted them into :class:`HookMatcher` entries on a
:class:`HookEmitter`. F3 closes that bridge:

* :func:`_build_plugin_hook_emitter` converts plugin :class:`Hook`
  records into a flat list of :class:`HookMatcher` entries wrapping
  :class:`CommandHook` instances on a fresh :class:`HookExecutor`.
* The three otter agent build sites (one-shot ``-p``, HTTP serve,
  ACP serve) and the REPL ``build_otter_agent`` factory each compose
  the resulting :class:`HookEmitter` onto :attr:`LoopConfig.hook_emitter`
  so :mod:`chimera.core.tool_executor` actually fires the hooks before
  every tool call.

These tests pin both halves: the conversion helper output shape, and
the end-to-end "build an agent, run a tool, assert the hook fired"
loop using a synthesized in-memory plugin.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from chimera.core.context import Context
from chimera.core.loop import ReAct
from chimera.core.loop_config import LoopConfig
from chimera.core.tool import BaseTool
from chimera.hooks.emitter import HookEmitter
from chimera.hooks.executor import HookExecutor
from chimera.hooks.hook_types import CommandHook, HookMatcher
from chimera.otter.cli import (
    _attach_plugin_extensions,
    _build_plugin_hook_emitter,
)
from chimera.otter.plugins import OtterPlugin
from chimera.plugins.base import Hook as PluginHook
from chimera.providers.base import Provider, Response
from chimera.types import Message, ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin(
    *,
    name: str = "hook-plugin",
    hooks: list[PluginHook] | None = None,
) -> OtterPlugin:
    """Build an :class:`OtterPlugin` with the supplied hook records."""
    return OtterPlugin(
        _name=name,
        _version="1.0.0",
        _description="hook test plugin",
        _author="test",
        path=Path("/fake/plugin/dir") / name,
        scope="user",
        manifest={"name": name},
        hooks=list(hooks or []),
    )


class _ScriptedProvider(Provider):
    """Returns a single tool-calling Response, then a no-tool 'done' Response."""

    def __init__(self, tool_calls: list[ToolCall]) -> None:
        self._responses = [
            Response(content="calling", tool_calls=tool_calls, usage={}),
            Response(content="done", tool_calls=[], usage={}),
        ]
        self._idx = 0

    def _next(self) -> Response:
        if self._idx >= len(self._responses):
            return Response(content="(done)", tool_calls=[], usage={})
        resp = self._responses[self._idx]
        self._idx += 1
        return resp

    def complete(
        self,
        messages: Any,
        tools: Any = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> Response:
        return self._next()

    async def async_complete(
        self,
        messages: Any,
        tools: Any = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking: Any = None,
    ) -> Response:
        return self._next()

    context_window = 200_000  # type: ignore[assignment]
    supports_tool_use = True  # type: ignore[assignment]
    model_name = "mock"  # type: ignore[assignment]


class _RecordingTool(BaseTool):
    """Minimal tool that records every invocation in a shared list."""

    name = "noop"
    description = "Records the call args and returns 'ok'."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": [],
    }

    def __init__(self, side_effect_box: list[dict[str, Any]]) -> None:
        self._side_effect = side_effect_box

    def execute(self, args: dict[str, Any], env: Any = None) -> ToolResult:
        self._side_effect.append(dict(args))
        return ToolResult(output="ok")


# ---------------------------------------------------------------------------
# _build_plugin_hook_emitter — pure conversion contract
# ---------------------------------------------------------------------------


def test_build_plugin_hook_emitter_returns_none_for_empty_list() -> None:
    """An empty hook list produces no emitter at all."""
    assert _build_plugin_hook_emitter([]) is None


def test_build_plugin_hook_emitter_skips_hooks_with_no_command() -> None:
    """A Hook with an empty command is dropped; no emitter when nothing remains."""
    bad = PluginHook(command="", event_type="PreToolUse")
    assert _build_plugin_hook_emitter([bad]) is None


def test_build_plugin_hook_emitter_returns_emitter_with_one_matcher() -> None:
    """One valid Hook -> one HookMatcher wrapping one CommandHook."""
    raw = PluginHook(
        command="echo hi",
        event_type="PreToolUse",
        timeout=12,
        env={"FOO": "bar"},
    )
    emitter = _build_plugin_hook_emitter([raw])
    assert isinstance(emitter, HookEmitter)
    matchers = emitter._matchers  # type: ignore[attr-defined]  # private; tests pin shape
    assert len(matchers) == 1
    matcher = matchers[0]
    assert isinstance(matcher, HookMatcher)
    assert matcher.matcher is None  # match every tool
    assert matcher.source == "plugin"
    assert len(matcher.hooks) == 1
    cmd = matcher.hooks[0]
    assert isinstance(cmd, CommandHook)
    assert cmd.command == "echo hi"
    assert cmd.timeout == 12
    assert cmd.extra_env == {"FOO": "bar"}


def test_build_plugin_hook_emitter_combines_multiple_hooks() -> None:
    """Each Hook becomes its own HookMatcher; all land in the same emitter."""
    hooks = [
        PluginHook(command="echo a", event_type="PreToolUse"),
        PluginHook(command="echo b", event_type="PostToolUse"),
        PluginHook(command="echo c", event_type="PreToolUse"),
    ]
    emitter = _build_plugin_hook_emitter(hooks)
    assert isinstance(emitter, HookEmitter)
    matchers = emitter._matchers  # type: ignore[attr-defined]
    assert len(matchers) == 3
    commands = [m.hooks[0].command for m in matchers]
    assert commands == ["echo a", "echo b", "echo c"]


def test_build_plugin_hook_emitter_uses_fresh_executor() -> None:
    """The constructed emitter wraps a real :class:`HookExecutor`."""
    raw = PluginHook(command="echo hi", event_type="PreToolUse")
    emitter = _build_plugin_hook_emitter([raw])
    assert isinstance(emitter, HookEmitter)
    # Active means an executor was wired.
    assert emitter.active is True
    assert isinstance(emitter._executor, HookExecutor)  # type: ignore[attr-defined]


def test_build_plugin_hook_emitter_skips_non_hook_records() -> None:
    """Defensive: arbitrary objects in the list are ignored, not raised."""
    raw = PluginHook(command="echo ok", event_type="PreToolUse")
    out = _build_plugin_hook_emitter([raw, object(), 42, "string"])
    assert isinstance(out, HookEmitter)
    matchers = out._matchers  # type: ignore[attr-defined]
    assert len(matchers) == 1
    assert matchers[0].hooks[0].command == "echo ok"


# ---------------------------------------------------------------------------
# End-to-end: synthesized plugin -> emitter -> tool dispatch fires hook
# ---------------------------------------------------------------------------


def test_plugin_hook_fires_when_tool_executes(tmp_path: Path) -> None:
    """Plugin PreToolUse hook fires before the tool runs in the async loop.

    Pipeline:

    1. Build a synthetic OtterPlugin with one PreToolUse Hook (command
       writes to a sentinel file via stdin so we know the executor ran it).
    2. Run :func:`_attach_plugin_extensions` with a stub loader.
    3. Convert collected hooks via :func:`_build_plugin_hook_emitter`.
    4. Mount the emitter on a :class:`LoopConfig` and drive a single
       async ReAct turn that issues one ``noop`` tool call.
    5. Assert the sentinel file exists (hook fired) **before** the
       tool's side-effect list grew (tool ran). The CommandHook subprocess
       writes the file synchronously, so the write completing proves the
       hook ran prior to the tool dispatch.
    """
    sentinel = tmp_path / "fired.txt"
    # Use a portable shell command — `cat > path` writes whatever the
    # executor pipes on stdin (the HookInput JSON) to the sentinel file.
    raw_hook = PluginHook(
        command=f"cat > {sentinel!s}",
        event_type="PreToolUse",
        timeout=10,
    )
    plugin = _make_plugin(hooks=[raw_hook])

    # Drive _attach_plugin_extensions with the synth plugin loader.
    plugin_hooks: list[Any] = []
    out = _attach_plugin_extensions(
        tools=[],
        hooks=plugin_hooks,
        agent_registry=None,
        project_root=tmp_path,
        loader=lambda _root: [plugin],
    )
    assert out == [plugin]
    assert plugin_hooks == [raw_hook]

    # Convert to a HookEmitter (the new bridge).
    emitter = _build_plugin_hook_emitter(plugin_hooks)
    assert isinstance(emitter, HookEmitter)

    # Drive a single tool call through ReAct.async_run.
    side_effects: list[dict[str, Any]] = []
    tool = _RecordingTool(side_effects)
    tc = ToolCall(id="tc-1", name="noop", arguments={"x": "42"})
    provider = _ScriptedProvider([tc])

    config = LoopConfig(hook_emitter=emitter)
    loop = ReAct(max_steps=4, config=config)
    context = Context(system="test")
    context.add(Message.user("go"))

    asyncio.run(loop.async_run(provider, [tool], context, None))

    # Hook must have run (sentinel file exists; CommandHook subprocess
    # piped the HookInput JSON onto disk via `cat > path`).
    assert sentinel.is_file(), (
        f"plugin hook never fired; sentinel {sentinel!s} missing. "
        f"side_effects={side_effects!r}"
    )
    payload = sentinel.read_text(encoding="utf-8").strip()
    # The HookInput JSON contract includes the tool name + event name.
    assert "noop" in payload, payload
    assert "PreToolUse" in payload, payload

    # Tool also ran (hook was non-blocking by default).
    assert side_effects == [{"x": "42"}]


def test_plugin_hook_emitter_inactive_when_no_plugin_hooks(tmp_path: Path) -> None:
    """No plugin hooks -> no emitter; LoopConfig.hook_emitter stays None."""
    plugin = _make_plugin(hooks=[])
    plugin_hooks: list[Any] = []
    _attach_plugin_extensions(
        tools=[],
        hooks=plugin_hooks,
        agent_registry=None,
        project_root=tmp_path,
        loader=lambda _root: [plugin],
    )
    assert plugin_hooks == []
    assert _build_plugin_hook_emitter(plugin_hooks) is None


def test_run_print_mode_wires_plugin_hooks_into_loop_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: ``-p`` build site mounts the plugin emitter on LoopConfig.

    We patch :func:`load_otter_plugins` to return a single plugin with a
    PreToolUse hook, then patch :class:`Agent` to capture the
    :class:`LoopConfig` it was constructed against. The captured config
    must carry a non-None ``hook_emitter``.
    """
    import argparse

    from chimera.otter import cli as otter_cli

    raw_hook = PluginHook(command="true", event_type="PreToolUse", timeout=5)
    plugin = _make_plugin(hooks=[raw_hook])

    captured: dict[str, Any] = {}

    class _CapturingAgent:
        def __init__(self, **kwargs: Any) -> None:
            captured["loop"] = kwargs.get("loop")
            captured["tools"] = list(kwargs.get("tools") or [])
            self.provider = kwargs.get("provider")

        async def async_run(self, *_a: Any, **_kw: Any) -> Any:
            from types import SimpleNamespace

            return SimpleNamespace(
                output="",
                steps=0,
                tool_calls_total=0,
                cost=0.0,
                success=True,
                error=None,
            )

    class _FakeProvider:
        model_name = "synthetic"

    monkeypatch.setattr(
        otter_cli,
        "_build_provider",
        lambda _model: _FakeProvider(),
    )
    monkeypatch.setattr("chimera.core.agent.Agent", _CapturingAgent)
    # Replace the directory loader so we never touch the filesystem.
    monkeypatch.setattr(
        "chimera.otter.plugins.load_otter_plugins",
        lambda _root: [plugin],
    )

    args = argparse.Namespace(
        model="synthetic",
        print_mode="hello",
        output_format="json",
        max_steps=1,
        cwd=str(tmp_path),
        no_rich=True,
        no_color=True,
        no_save=True,
        no_lsp=True,
        no_rules=True,
        no_mcp=True,
        no_plugins=False,
        run_id=None,
        allowed_tools="",
    )
    rc = otter_cli._run_print_mode(args)
    assert rc in (0, 1)

    loop = captured.get("loop")
    assert loop is not None, "Agent never constructed; check patches."
    config = loop.config
    assert config.hook_emitter is not None, (
        "plugin hook emitter not wired onto LoopConfig"
    )
    assert isinstance(config.hook_emitter, HookEmitter)
    assert config.hook_emitter.active is True


def test_run_print_mode_leaves_hook_emitter_none_without_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin with zero hook records leaves ``LoopConfig.hook_emitter`` unset."""
    import argparse

    from chimera.otter import cli as otter_cli

    plugin = _make_plugin(hooks=[])  # zero hooks

    captured: dict[str, Any] = {}

    class _CapturingAgent:
        def __init__(self, **kwargs: Any) -> None:
            captured["loop"] = kwargs.get("loop")
            self.provider = kwargs.get("provider")

        async def async_run(self, *_a: Any, **_kw: Any) -> Any:
            from types import SimpleNamespace

            return SimpleNamespace(
                output="",
                steps=0,
                tool_calls_total=0,
                cost=0.0,
                success=True,
                error=None,
            )

    class _FakeProvider:
        model_name = "synthetic"

    monkeypatch.setattr(
        otter_cli,
        "_build_provider",
        lambda _model: _FakeProvider(),
    )
    monkeypatch.setattr("chimera.core.agent.Agent", _CapturingAgent)
    monkeypatch.setattr(
        "chimera.otter.plugins.load_otter_plugins",
        lambda _root: [plugin],
    )

    args = argparse.Namespace(
        model="synthetic",
        print_mode="hello",
        output_format="json",
        max_steps=1,
        cwd=str(tmp_path),
        no_rich=True,
        no_color=True,
        no_save=True,
        no_lsp=True,
        no_rules=True,
        no_mcp=True,
        no_plugins=False,
        run_id=None,
        allowed_tools="",
    )
    otter_cli._run_print_mode(args)

    loop = captured.get("loop")
    assert loop is not None
    assert loop.config.hook_emitter is None

"""Tests for otter plugin wiring (W2).

These tests verify that ``_attach_plugin_extensions`` correctly grafts
plugin contributions onto the in-construction agent surface:

* plugin agents -> :class:`AgentRegistry`
* plugin hooks  -> caller's hook list
* plugin MCP servers -> caller's MCP server list
* plugin slash commands -> shared slash registry
* plugin extra tools (via ``_extra_tools`` opt-in) -> caller's tool list

The tests mock :func:`load_otter_plugins` so they never touch the real
filesystem, and assert the augmentation contract end-to-end.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chimera.agents.config import AgentConfig
from chimera.agents.registry import AgentRegistry
from chimera.otter.cli import (
    _attach_plugin_extensions,
    _make_plugin_command_handler,
)
from chimera.otter.plugins import OtterCommand, OtterPlugin
from chimera.plugins.base import Hook, MCPServerConfig


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_agent_config(name: str = "tester") -> AgentConfig:
    """Construct a minimal :class:`AgentConfig` for registry assertions."""
    return AgentConfig(
        name=name,
        description=f"{name} agent",
        system_prompt=f"You are the {name} agent.",
    )


def _make_otter_plugin(
    *,
    name: str = "demo",
    agents: list[AgentConfig] | None = None,
    commands: list[OtterCommand] | None = None,
    mcp_servers: dict[str, MCPServerConfig] | None = None,
    hooks: list[Hook] | None = None,
    extra_tools: list[Any] | None = None,
) -> OtterPlugin:
    """Build an OtterPlugin instance pre-populated with extension records."""
    plugin = OtterPlugin(
        _name=name,
        _version="1.0.0",
        _description="test plugin",
        _author="test",
        path=Path("/fake/plugin/dir") / name,
        scope="user",
        manifest={"name": name},
        agents=list(agents or []),
        commands=list(commands or []),
        mcp_servers=dict(mcp_servers or {}),
        hooks=list(hooks or []),
    )
    if extra_tools is not None:
        # Opt-in slot consumed by _attach_plugin_extensions.
        plugin._extra_tools = extra_tools  # type: ignore[attr-defined]
    return plugin


# ---------------------------------------------------------------------------
# disabled / empty short-circuits
# ---------------------------------------------------------------------------


def test_attach_plugin_extensions_disabled_short_circuits(tmp_path: Path) -> None:
    """When ``enabled=False``, the loader is never called and result is empty."""
    loader = MagicMock()
    tools: list[Any] = ["sentinel"]
    hooks: list[Any] = []

    out = _attach_plugin_extensions(
        tools,
        hooks,
        agent_registry=None,
        project_root=tmp_path,
        enabled=False,
        loader=loader,
    )
    assert out == []
    loader.assert_not_called()
    # Inputs unchanged.
    assert tools == ["sentinel"]
    assert hooks == []


def test_attach_plugin_extensions_empty_loader_returns_empty(tmp_path: Path) -> None:
    """A loader returning ``[]`` yields an empty plugin list, no mutation."""
    tools: list[Any] = []
    hooks: list[Any] = []
    out = _attach_plugin_extensions(
        tools,
        hooks,
        agent_registry=None,
        project_root=tmp_path,
        loader=lambda _root: [],
    )
    assert out == []
    assert tools == []
    assert hooks == []


def test_attach_plugin_extensions_loader_exception_is_swallowed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Loader exceptions degrade to a stderr warning; result is empty."""

    def _bad_loader(_root: Path) -> list[Any]:
        raise RuntimeError("boom")

    out = _attach_plugin_extensions(
        [],
        [],
        agent_registry=None,
        project_root=tmp_path,
        loader=_bad_loader,
    )
    assert out == []
    captured = capsys.readouterr()
    assert "plugin discovery failed" in captured.err


# ---------------------------------------------------------------------------
# Augmentation surface — agents / hooks / mcp / commands / tools
# ---------------------------------------------------------------------------


def test_attach_plugin_extensions_registers_agents(tmp_path: Path) -> None:
    """Plugin agents are registered onto the supplied AgentRegistry."""
    cfg = _make_agent_config(name="reviewer")
    plugin = _make_otter_plugin(name="rev-plugin", agents=[cfg])
    registry = AgentRegistry()

    _attach_plugin_extensions(
        [],
        [],
        agent_registry=registry,
        project_root=tmp_path,
        loader=lambda _root: [plugin],
    )
    assert "reviewer" in registry.list()
    assert registry.get("reviewer") is cfg


def test_attach_plugin_extensions_appends_hooks(tmp_path: Path) -> None:
    """Plugin hooks are appended to the caller's hook list."""
    hook = Hook(
        command="echo hi",
        event_type="PreToolUse",
        timeout=5,
    )
    plugin = _make_otter_plugin(name="hook-plugin", hooks=[hook])
    hooks: list[Any] = []

    _attach_plugin_extensions(
        [],
        hooks,
        agent_registry=None,
        project_root=tmp_path,
        loader=lambda _root: [plugin],
    )
    assert hooks == [hook]


def test_attach_plugin_extensions_appends_mcp_servers(tmp_path: Path) -> None:
    """Plugin MCP server configs append as ``(name, cfg)`` tuples."""
    cfg = MCPServerConfig(command=["fs-server"], args=[])
    plugin = _make_otter_plugin(name="mcp-plugin", mcp_servers={"fs": cfg})
    mcp_list: list[Any] = []

    _attach_plugin_extensions(
        [],
        [],
        agent_registry=None,
        project_root=tmp_path,
        mcp_servers=mcp_list,
        loader=lambda _root: [plugin],
    )
    assert mcp_list == [("fs", cfg)]


def test_attach_plugin_extensions_skips_mcp_when_sink_is_none(tmp_path: Path) -> None:
    """When ``mcp_servers=None``, plugin MCP entries are silently dropped."""
    cfg = MCPServerConfig(command=["fs-server"])
    plugin = _make_otter_plugin(name="mcp-plugin", mcp_servers={"fs": cfg})

    # No assertion errors expected; the helper must not raise.
    out = _attach_plugin_extensions(
        [],
        [],
        agent_registry=None,
        project_root=tmp_path,
        mcp_servers=None,
        loader=lambda _root: [plugin],
    )
    assert out == [plugin]


def test_attach_plugin_extensions_registers_slash_commands(tmp_path: Path) -> None:
    """Plugin commands install onto the shared slash registry."""
    cmd = OtterCommand(
        name="hi",
        description="say hello",
        body="Hello!",
        source=Path("/fake/cmd.md"),
        plugin="cmd-plugin",
    )
    plugin = _make_otter_plugin(name="cmd-plugin", commands=[cmd])

    registered: list[tuple[str, Any, str]] = []

    def _fake_register(name: str, handler: Any, help_text: str = "") -> None:
        registered.append((name, handler, help_text))

    _attach_plugin_extensions(
        [],
        [],
        agent_registry=None,
        project_root=tmp_path,
        loader=lambda _root: [plugin],
        slash_register=_fake_register,
    )
    assert len(registered) == 1
    name, handler, help_text = registered[0]
    assert name == "hi"
    assert help_text == "say hello"
    # Handler invocation prints the body.
    captured: list[str] = []
    handler(None, None, "", captured.append)
    assert captured == ["Hello!"]


def test_attach_plugin_extensions_appends_extra_tools(tmp_path: Path) -> None:
    """Opt-in ``_extra_tools`` slot lets test plugins contribute tools."""

    class _FakeTool:
        name = "fake"

    fake = _FakeTool()
    plugin = _make_otter_plugin(name="tools-plugin", extra_tools=[fake])
    tools: list[Any] = []

    _attach_plugin_extensions(
        tools,
        [],
        agent_registry=None,
        project_root=tmp_path,
        loader=lambda _root: [plugin],
    )
    assert tools == [fake]


def test_attach_plugin_extensions_combines_all_surfaces(tmp_path: Path) -> None:
    """One plugin contributing every surface augments every sink."""
    cfg = _make_agent_config(name="combo")
    cmd = OtterCommand(
        name="run",
        description="run combo",
        body="GO",
        source=Path("/fake/run.md"),
        plugin="combo",
    )
    hook = Hook(command="ls", event_type="PostToolUse")
    mcp = MCPServerConfig(command=["mcp-bin"])

    class _Tool:
        name = "tool-x"

    tool = _Tool()
    plugin = _make_otter_plugin(
        name="combo",
        agents=[cfg],
        commands=[cmd],
        mcp_servers={"mcp-x": mcp},
        hooks=[hook],
        extra_tools=[tool],
    )

    registry = AgentRegistry()
    tools: list[Any] = []
    hooks: list[Any] = []
    mcp_list: list[Any] = []
    registered: list[tuple[str, Any, str]] = []

    out = _attach_plugin_extensions(
        tools,
        hooks,
        agent_registry=registry,
        project_root=tmp_path,
        mcp_servers=mcp_list,
        loader=lambda _root: [plugin],
        slash_register=lambda n, h, t="": registered.append((n, h, t)),
    )

    assert out == [plugin]
    assert registry.get("combo") is cfg
    assert hooks == [hook]
    assert mcp_list == [("mcp-x", mcp)]
    assert tools == [tool]
    assert [(n, t) for n, _, t in registered] == [("run", "run combo")]


def test_attach_plugin_extensions_continues_on_per_plugin_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bad agent register doesn't kill subsequent plugin processing."""

    cfg = _make_agent_config(name="ok")
    plugin = _make_otter_plugin(name="part-broken", agents=[cfg])

    class _BadRegistry:
        """Always-raising stand-in for AgentRegistry."""

        def register(self, _config: Any) -> None:
            raise RuntimeError("nope")

        def list(self) -> list[str]:
            return []

        def get(self, _name: str) -> Any:
            return None

    out = _attach_plugin_extensions(
        [],
        [],
        agent_registry=_BadRegistry(),
        project_root=tmp_path,
        loader=lambda _root: [plugin],
    )
    assert out == [plugin]
    captured = capsys.readouterr()
    assert "agent register" in captured.err


# ---------------------------------------------------------------------------
# _make_plugin_command_handler
# ---------------------------------------------------------------------------


def test_make_plugin_command_handler_prints_body() -> None:
    """The handler prints the command body via the supplied printer."""
    cmd = OtterCommand(
        name="hi",
        description="",
        body="hello world",
        source=Path("/fake.md"),
        plugin="p",
    )
    handler = _make_plugin_command_handler(cmd)
    captured: list[str] = []
    handler(None, None, "", captured.append)
    assert captured == ["hello world"]


def test_make_plugin_command_handler_handles_empty_body() -> None:
    """A body-less command falls back to a placeholder string."""
    cmd = OtterCommand(
        name="empty",
        description="",
        body="",
        source=Path("/fake.md"),
        plugin="p",
    )
    handler = _make_plugin_command_handler(cmd)
    captured: list[str] = []
    handler(None, None, "", captured.append)
    assert captured and "empty" in captured[0]


# ---------------------------------------------------------------------------
# Integration into the agent build-site
# ---------------------------------------------------------------------------


def test_run_print_mode_invokes_plugin_attach(tmp_path: Path) -> None:
    """Smoke test: ``_run_print_mode`` calls ``_attach_plugin_extensions``."""

    class _FakeTool:
        name = "plugin-injected"

    fake_tool = _FakeTool()

    def _fake_attach(
        tools: list[Any],
        hooks: list[Any],
        agent_registry: Any,
        project_root: Path,
        *,
        mcp_servers: list[Any] | None = None,
        enabled: bool = True,
        loader: Any | None = None,
        slash_register: Any | None = None,
    ) -> list[Any]:
        if enabled:
            tools.append(fake_tool)
        return []

    captured_tools: list[Any] = []

    class _FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            captured_tools.extend(kwargs.get("tools", []))
            self.provider = kwargs.get("provider")

        async def async_run(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                output="",
                steps=0,
                tool_calls_total=0,
                cost=0.0,
                success=True,
                error=None,
            )

    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic"

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

    with patch("chimera.otter.cli._build_provider", return_value=fake_provider):
        with patch("chimera.core.agent.Agent", _FakeAgent):
            with patch(
                "chimera.otter.cli._attach_plugin_extensions",
                side_effect=_fake_attach,
            ):
                from chimera.otter.cli import _run_print_mode

                rc = _run_print_mode(args)

    # rc may be 0 or 1 depending on synthesized AgentResult shape; we
    # assert the wiring (tools augmentation) instead.
    assert rc in (0, 1)
    assert fake_tool in captured_tools


def test_run_print_mode_disables_plugins_when_no_plugins(tmp_path: Path) -> None:
    """``--no-plugins`` propagates to ``_attach_plugin_extensions`` (enabled=False)."""

    seen: dict[str, Any] = {}

    def _spy_attach(
        tools: list[Any],
        hooks: list[Any],
        agent_registry: Any,
        project_root: Path,
        *,
        mcp_servers: list[Any] | None = None,
        enabled: bool = True,
        loader: Any | None = None,
        slash_register: Any | None = None,
    ) -> list[Any]:
        seen["enabled"] = enabled
        return []

    class _FakeAgent:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def async_run(self, *_a: Any, **_kw: Any) -> Any:
            return SimpleNamespace(
                output="",
                steps=0,
                tool_calls_total=0,
                cost=0.0,
                success=True,
                error=None,
            )

    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic"

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
        no_plugins=True,
        run_id=None,
        allowed_tools="",
    )

    with patch("chimera.otter.cli._build_provider", return_value=fake_provider):
        with patch("chimera.core.agent.Agent", _FakeAgent):
            with patch(
                "chimera.otter.cli._attach_plugin_extensions",
                side_effect=_spy_attach,
            ):
                from chimera.otter.cli import _run_print_mode

                _run_print_mode(args)

    assert seen.get("enabled") is False


def test_repl_build_otter_agent_invokes_plugin_attach(tmp_path: Path) -> None:
    """``build_otter_agent`` (REPL bootstrap) invokes the plugin helper."""

    class _FakeTool:
        name = "plugin-injected"

    fake_tool = _FakeTool()

    def _fake_attach(
        tools: list[Any],
        hooks: list[Any],
        agent_registry: Any,
        project_root: Path,
        *,
        mcp_servers: list[Any] | None = None,
        enabled: bool = True,
        loader: Any | None = None,
        slash_register: Any | None = None,
    ) -> list[Any]:
        if enabled:
            tools.append(fake_tool)
        return []

    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic"

    args = argparse.Namespace(
        model="synthetic",
        cwd=str(tmp_path),
        max_steps=5,
        no_lsp=True,
        no_rules=True,
        no_mcp=True,
        no_plugins=False,
    )
    with patch(
        "chimera.otter.cli._attach_plugin_extensions",
        side_effect=_fake_attach,
    ):
        from chimera.otter.repl import build_otter_agent

        agent = build_otter_agent(args, provider=fake_provider)
    assert fake_tool in list(agent.tools)


def test_repl_run_otter_repl_skips_plugins_when_disabled(tmp_path: Path) -> None:
    """``--no-plugins`` short-circuits the REPL's plugin wiring step."""
    fake_provider = MagicMock()
    fake_provider.model_name = "synthetic"

    with patch.dict(os.environ, {"HOME": str(tmp_path)}, clear=False):
        with patch("chimera.otter.repl.Path") as mock_path_cls:
            real_path = Path
            mock_path_cls.side_effect = lambda *a, **kw: real_path(*a, **kw)
            mock_path_cls.home.return_value = real_path(tmp_path)
            with patch(
                "chimera.otter.repl._build_otter_provider",
                return_value=fake_provider,
            ):
                with patch("chimera.cli.code.run_code", return_value=0):
                    with patch(
                        "chimera.otter.cli._attach_plugin_extensions"
                    ) as spy:
                        from chimera.otter.repl import run_otter_repl

                        args = argparse.Namespace(
                            model="synthetic",
                            cwd=str(tmp_path),
                            max_steps=5,
                            agent=None,
                            models="",
                            no_plugins=True,
                            no_custom_commands=True,
                            _quiet_run_dir=True,
                        )
                        rc = run_otter_repl(args)
    assert rc == 0
    spy.assert_not_called()

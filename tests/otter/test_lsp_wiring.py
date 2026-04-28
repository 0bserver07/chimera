"""Tests for the W5 LSP-tools-by-default wiring.

Covers the four assembly sites that compose the otter agent's tool list:

* :func:`chimera.otter.cli._run_print_mode` — one-shot ``-p`` path.
* :func:`chimera.otter.cli._dispatch_serve_http` — HTTP server factory.
* :func:`chimera.otter.cli._dispatch_serve_acp` — ACP server factory.
* :func:`chimera.otter.repl.build_otter_agent` — REPL agent bootstrap.

The :func:`chimera.otter.lsp.build_lsp_tool_group` factory is mocked at
each callsite so we never spawn a real language server. We assert:

1. The default (``--no-lsp`` absent) path appends LSP tools to the
   default agent tool group.
2. ``--no-lsp`` short-circuits and never calls the factory.
3. A factory failure (e.g. no language server detected) does NOT raise
   and falls through to the unaltered tool list with a stderr warning.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from chimera.core.tool_group import ToolGroup
from chimera.otter import cli as otter_cli
from chimera.otter import repl as otter_repl


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _fake_lsp_tool(name: str) -> MagicMock:
    """Return a tool-shaped mock with a ``.name`` attribute."""
    tool = MagicMock(name=f"FakeTool({name})")
    tool.name = name
    return tool


def _fake_lsp_group() -> ToolGroup:
    """A small fake LSP tool group that mirrors the real factory shape."""
    return ToolGroup(
        "otter-lsp",
        [
            _fake_lsp_tool("lsp_diagnostics"),
            _fake_lsp_tool("lsp_completion"),
            _fake_lsp_tool("lsp_rename"),
            _fake_lsp_tool("lsp_definition"),
            _fake_lsp_tool("lsp_references"),
        ],
    )


# ---------------------------------------------------------------------------
# _attach_lsp_tools — direct unit tests
# ---------------------------------------------------------------------------


def test_attach_lsp_tools_appends_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """With ``no_lsp=False`` the LSP tools are appended to ``tools``."""
    factory = MagicMock(return_value=_fake_lsp_group())
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)

    base = [_fake_lsp_tool("read"), _fake_lsp_tool("write")]
    result = otter_cli._attach_lsp_tools(
        base, no_lsp=False, project_root=tmp_path,
    )
    names = [t.name for t in result]
    assert names[:2] == ["read", "write"]
    assert {"lsp_diagnostics", "lsp_completion", "lsp_rename",
            "lsp_definition", "lsp_references"} <= set(names)
    assert len(result) == len(base) + 5
    factory.assert_called_once()
    # base must not be mutated
    assert len(base) == 2


def test_attach_lsp_tools_short_circuits_when_no_lsp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``--no-lsp`` (no_lsp=True) skips the factory entirely."""
    factory = MagicMock(side_effect=AssertionError("must not be called"))
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)

    base = [_fake_lsp_tool("read")]
    result = otter_cli._attach_lsp_tools(
        base, no_lsp=True, project_root=tmp_path,
    )
    assert [t.name for t in result] == ["read"]
    factory.assert_not_called()


def test_attach_lsp_tools_swallows_detection_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Factory failure (no language server) must NOT raise — warn + fall through."""
    factory = MagicMock(side_effect=RuntimeError("no language server on PATH"))
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)

    base = [_fake_lsp_tool("read"), _fake_lsp_tool("write")]
    # Must not raise.
    result = otter_cli._attach_lsp_tools(
        base, no_lsp=False, project_root=tmp_path,
    )
    # Returns the original tool list, no LSP additions.
    assert [t.name for t in result] == ["read", "write"]
    captured = capsys.readouterr()
    assert "LSP detection failed" in captured.err
    assert "no language server on PATH" in captured.err


def test_attach_lsp_tools_returns_new_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The helper must return a new list, never mutate the caller's."""
    factory = MagicMock(return_value=_fake_lsp_group())
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)

    base = [_fake_lsp_tool("read")]
    result = otter_cli._attach_lsp_tools(
        base, no_lsp=False, project_root=tmp_path,
    )
    assert result is not base
    assert len(base) == 1


# ---------------------------------------------------------------------------
# CLI flag — argparse surface
# ---------------------------------------------------------------------------


def test_cli_no_lsp_flag_default_off() -> None:
    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    args = parser.parse_args([])
    assert hasattr(args, "no_lsp")
    assert args.no_lsp is False


def test_cli_no_lsp_flag_can_be_set() -> None:
    parser = argparse.ArgumentParser()
    otter_cli.add_arguments(parser)
    args = parser.parse_args(["--no-lsp", "-p", "hi"])
    assert args.no_lsp is True


# ---------------------------------------------------------------------------
# Wiring: _run_print_mode
# ---------------------------------------------------------------------------


def _make_print_args(tmp_path: Path, *, no_lsp: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        model="claude-sonnet-4-6",
        print_mode="hi",
        output_format="text",
        max_steps=1,
        cwd=str(tmp_path),
        allowed_tools="",
        no_rich=True,
        no_color=True,
        no_save=True,
        no_lsp=no_lsp,
        no_rules=True,
        run_id=None,
        acp=False,
        host=None,
        port=None,
        auth_token=None,
        subcommand=None,
        sub_action=None,
        sub_target=None,
        bench_limit=5,
        bench_domain="airline",
    )


def _patch_agent_machinery(monkeypatch: pytest.MonkeyPatch, *, captured: dict[str, Any]) -> None:
    """Stub Agent + provider + asyncio.run so _run_print_mode is hermetic."""

    class _StubProvider:
        model_name = "claude-sonnet-4-6"

    class _StubResult:
        output = "ok"
        steps = 1
        cost = 0.0
        success = True
        tool_calls_total = 0
        error = None

    class _StubAgent:
        def __init__(self, *, provider: Any, tools: list[Any], loop: Any, prompt: Any) -> None:
            captured["tools"] = list(tools)

        async def async_run(self, prompt: str, env: Any) -> Any:
            return _StubResult()

    monkeypatch.setattr(otter_cli, "_build_provider", lambda model: _StubProvider())
    monkeypatch.setattr("chimera.core.agent.Agent", _StubAgent)


def test_run_print_mode_attaches_lsp_tools_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    _patch_agent_machinery(monkeypatch, captured=captured)
    factory = MagicMock(return_value=_fake_lsp_group())
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)

    args = _make_print_args(tmp_path, no_lsp=False)
    rc = otter_cli._run_print_mode(args)
    assert rc == 0
    factory.assert_called_once()
    names = {t.name for t in captured["tools"]}
    assert {"lsp_diagnostics", "lsp_completion", "lsp_rename",
            "lsp_definition", "lsp_references"} <= names


def test_run_print_mode_no_lsp_short_circuits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}
    _patch_agent_machinery(monkeypatch, captured=captured)
    factory = MagicMock(side_effect=AssertionError("must not be called"))
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)

    args = _make_print_args(tmp_path, no_lsp=True)
    rc = otter_cli._run_print_mode(args)
    assert rc == 0
    factory.assert_not_called()
    names = {t.name for t in captured["tools"]}
    # No LSP tools were added.
    assert "lsp_diagnostics" not in names


def test_run_print_mode_lsp_failure_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}
    _patch_agent_machinery(monkeypatch, captured=captured)
    monkeypatch.setattr(
        "chimera.otter.lsp.build_lsp_tool_group",
        MagicMock(side_effect=RuntimeError("no LSP server")),
    )

    args = _make_print_args(tmp_path, no_lsp=False)
    rc = otter_cli._run_print_mode(args)
    assert rc == 0
    err = capsys.readouterr().err
    assert "LSP detection failed" in err
    names = {t.name for t in captured["tools"]}
    assert "lsp_diagnostics" not in names


# ---------------------------------------------------------------------------
# Wiring: build_otter_agent (REPL bootstrap)
# ---------------------------------------------------------------------------


class _StubProvider:
    model_name = "claude-sonnet-4-6"


class _AgentSpy:
    def __init__(self, *, provider: Any, tools: list[Any], loop: Any, prompt: Any) -> None:
        self.provider = provider
        self.tools = list(tools)
        self.loop = loop
        self.prompt = prompt


def _make_repl_args(tmp_path: Path, *, no_lsp: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        model="claude-sonnet-4-6",
        cwd=str(tmp_path),
        max_steps=1,
        no_lsp=no_lsp,
    )


def test_build_otter_agent_attaches_lsp_tools_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    factory = MagicMock(return_value=_fake_lsp_group())
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)
    monkeypatch.setattr("chimera.core.agent.Agent", _AgentSpy)

    args = _make_repl_args(tmp_path, no_lsp=False)
    agent = otter_repl.build_otter_agent(args, provider=_StubProvider())
    factory.assert_called_once()
    names = {t.name for t in agent.tools}
    assert {"lsp_diagnostics", "lsp_completion", "lsp_rename",
            "lsp_definition", "lsp_references"} <= names


def test_build_otter_agent_no_lsp_short_circuits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    factory = MagicMock(side_effect=AssertionError("must not be called"))
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)
    monkeypatch.setattr("chimera.core.agent.Agent", _AgentSpy)

    args = _make_repl_args(tmp_path, no_lsp=True)
    agent = otter_repl.build_otter_agent(args, provider=_StubProvider())
    factory.assert_not_called()
    names = {t.name for t in agent.tools}
    assert "lsp_diagnostics" not in names


def test_build_otter_agent_lsp_failure_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "chimera.otter.lsp.build_lsp_tool_group",
        MagicMock(side_effect=RuntimeError("no LSP server")),
    )
    monkeypatch.setattr("chimera.core.agent.Agent", _AgentSpy)

    args = _make_repl_args(tmp_path, no_lsp=False)
    agent = otter_repl.build_otter_agent(args, provider=_StubProvider())
    err = capsys.readouterr().err
    assert "LSP detection failed" in err
    names = {t.name for t in agent.tools}
    assert "lsp_diagnostics" not in names


# ---------------------------------------------------------------------------
# Wiring: serve HTTP / ACP factories — exercise the closure that builds tools
# ---------------------------------------------------------------------------


def test_dispatch_serve_http_factory_attaches_lsp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The HTTP factory must call _attach_lsp_tools on each session build."""
    factory = MagicMock(return_value=_fake_lsp_group())
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)
    monkeypatch.setattr(otter_cli, "_build_provider", lambda model: _StubProvider())
    monkeypatch.setattr("chimera.core.agent.Agent", _AgentSpy)

    captured_factory: dict[str, Any] = {}

    def _fake_serve_http(
        agent_factory: Any,
        *,
        host: str,
        port: int,
        auth_token: Any,
        **_kwargs: Any,
    ) -> int:
        captured_factory["fn"] = agent_factory
        return 0

    monkeypatch.setattr("chimera.otter.server.serve_http", _fake_serve_http)

    args = argparse.Namespace(
        model="m", cwd=str(tmp_path), max_steps=1,
        host="127.0.0.1", port=0, auth_token=None,
        no_lsp=False, acp=False, subcommand="serve",
    )
    rc = otter_cli._dispatch_serve_http(args)
    assert rc == 0

    # Drive the factory to confirm LSP tools are appended.
    state = MagicMock()
    state.working_dir = str(tmp_path)
    agent = captured_factory["fn"](state)
    factory.assert_called_once()
    names = {t.name for t in agent.tools}
    assert "lsp_diagnostics" in names


def test_dispatch_serve_http_factory_honors_no_lsp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    factory = MagicMock(side_effect=AssertionError("must not be called"))
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)
    monkeypatch.setattr(otter_cli, "_build_provider", lambda model: _StubProvider())
    monkeypatch.setattr("chimera.core.agent.Agent", _AgentSpy)

    captured_factory: dict[str, Any] = {}

    def _fake_serve_http(
        agent_factory: Any,
        *,
        host: str,
        port: int,
        auth_token: Any,
        **_kwargs: Any,
    ) -> int:
        captured_factory["fn"] = agent_factory
        return 0

    monkeypatch.setattr("chimera.otter.server.serve_http", _fake_serve_http)

    args = argparse.Namespace(
        model="m", cwd=str(tmp_path), max_steps=1,
        host="127.0.0.1", port=0, auth_token=None,
        no_lsp=True, acp=False, subcommand="serve",
    )
    rc = otter_cli._dispatch_serve_http(args)
    assert rc == 0
    state = MagicMock()
    state.working_dir = str(tmp_path)
    agent = captured_factory["fn"](state)
    factory.assert_not_called()
    names = {t.name for t in agent.tools}
    assert "lsp_diagnostics" not in names


def test_dispatch_serve_acp_factory_attaches_lsp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    factory = MagicMock(return_value=_fake_lsp_group())
    monkeypatch.setattr("chimera.otter.lsp.build_lsp_tool_group", factory)
    monkeypatch.setattr(otter_cli, "_build_provider", lambda model: _StubProvider())
    monkeypatch.setattr("chimera.core.agent.Agent", _AgentSpy)

    captured_factory: dict[str, Any] = {}

    def _fake_serve_stdio(agent_factory: Any) -> int:
        captured_factory["fn"] = agent_factory
        return 0

    monkeypatch.setattr("chimera.otter.acp.serve_stdio", _fake_serve_stdio)

    args = argparse.Namespace(
        model="m", cwd=str(tmp_path), max_steps=1,
        no_lsp=False, acp=True, subcommand="serve",
    )
    rc = otter_cli._dispatch_serve_acp(args)
    assert rc == 0
    state = MagicMock()
    state.working_dir = str(tmp_path)
    agent = captured_factory["fn"](state)
    factory.assert_called_once()
    names = {t.name for t in agent.tools}
    assert "lsp_diagnostics" in names


def test_dispatch_serve_acp_factory_lsp_failure_no_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "chimera.otter.lsp.build_lsp_tool_group",
        MagicMock(side_effect=RuntimeError("no LSP server")),
    )
    monkeypatch.setattr(otter_cli, "_build_provider", lambda model: _StubProvider())
    monkeypatch.setattr("chimera.core.agent.Agent", _AgentSpy)

    captured_factory: dict[str, Any] = {}

    def _fake_serve_stdio(agent_factory: Any) -> int:
        captured_factory["fn"] = agent_factory
        return 0

    monkeypatch.setattr("chimera.otter.acp.serve_stdio", _fake_serve_stdio)

    args = argparse.Namespace(
        model="m", cwd=str(tmp_path), max_steps=1,
        no_lsp=False, acp=True, subcommand="serve",
    )
    rc = otter_cli._dispatch_serve_acp(args)
    assert rc == 0
    state = MagicMock()
    state.working_dir = str(tmp_path)
    # Must not raise even though build_lsp_tool_group blew up.
    agent = captured_factory["fn"](state)
    err = capsys.readouterr().err
    assert "LSP detection failed" in err
    names = {t.name for t in agent.tools}
    assert "lsp_diagnostics" not in names

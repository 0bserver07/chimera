"""Tests for MCP runtime wiring in :mod:`chimera.otter.cli`.

The wave-1 :mod:`chimera.otter.mcp` loader produced :class:`MCPServerConfig`
entries; wave-2 wires them into the agent's tool group at four assembly
sites (``-p`` one-shot, ``serve --acp``, ``serve`` HTTP, and the REPL's
``build_otter_agent`` factory). These tests cover that wiring at the helper
level (``_attach_mcp_tools``) plus the per-site short-circuit on ``--no-mcp``.

We never spawn a real MCP subprocess. ``load_mcp_servers`` is monkey-patched
to return synthetic configs and ``MCPClient`` is replaced with a fake whose
``add_from_spec`` / ``connect_all`` / ``tools`` are recordable, so each test
asserts the *contract* between :mod:`chimera.otter.mcp` and the live MCP
client without touching the real transport layer.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from chimera.otter import cli as otter_cli
from chimera.otter.mcp import MCPServerConfig


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeTool:
    """Minimal stand-in for :class:`chimera.core.tool.BaseTool`.

    The wiring path only inspects ``client.tools`` length and identity, so
    a name-and-len facade is enough; we never invoke the tool.
    """

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeMCPClient:
    """Records spec registrations + connect calls; exposes a tools list.

    Mirrors the surface the real :class:`chimera.mcp.client.MCPClient` exposes
    to ``_attach_mcp_tools``: ``add_from_spec``, ``connect_all``, and the
    ``tools`` property. Tests inspect the recorded calls directly.
    """

    def __init__(self) -> None:
        self.added: list[tuple[str, dict[str, Any]]] = []
        self.connect_calls: int = 0
        self._tools: list[_FakeTool] = []

    def add_from_spec(self, name: str, spec: dict[str, Any]) -> None:
        self.added.append((name, spec))

    def connect_all(self) -> None:
        self.connect_calls += 1
        # Materialize one fake tool per registered server so the augmented
        # list grows in a deterministic, name-keyed way.
        self._tools = [_FakeTool(f"mcp.{n}.echo") for n, _ in self.added]

    @property
    def tools(self) -> list[_FakeTool]:
        return list(self._tools)


@pytest.fixture
def patch_mcp(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install fake ``load_mcp_servers`` + ``MCPClient`` for ``_attach_mcp_tools``.

    Returns a dict with knobs each test mutates:
        * ``servers``: the list ``load_mcp_servers`` returns.
        * ``client_factory``: the constructor used in place of
          ``MCPClient``; defaults to :class:`_FakeMCPClient`.
        * ``last_client``: the most recently constructed fake (populated
          when the helper calls the factory).
    """
    state: dict[str, Any] = {
        "servers": [],
        "client_factory": _FakeMCPClient,
        "last_client": None,
    }

    def _fake_load(_root: Path) -> list[MCPServerConfig]:
        return list(state["servers"])

    def _fake_factory() -> Any:
        client = state["client_factory"]()
        state["last_client"] = client
        return client

    # Patch the loader at its source module so ``_attach_mcp_tools``'s
    # ``from chimera.otter.mcp import load_mcp_servers`` returns ours.
    monkeypatch.setattr("chimera.otter.mcp.load_mcp_servers", _fake_load)
    # Patch the MCPClient class similarly. The helper does
    # ``from chimera.mcp.client import MCPClient``; intercepting at the
    # source module covers every caller.
    monkeypatch.setattr("chimera.mcp.client.MCPClient", _fake_factory)
    return state


@pytest.fixture
def args_namespace() -> argparse.Namespace:
    """Default parsed namespace with ``--no-mcp`` off and a tmp cwd populated later."""
    return argparse.Namespace(
        no_mcp=False,
        no_lsp=True,  # short-circuit LSP so tests don't probe language servers.
        no_rules=True,  # short-circuit rules ingest for the same reason.
        cwd=None,
    )


# ---------------------------------------------------------------------------
# add_arguments: --no-mcp surface
# ---------------------------------------------------------------------------


def test_add_arguments_registers_no_mcp() -> None:
    """``--no-mcp`` is parseable on the otter parser."""
    parser = argparse.ArgumentParser(prog="chimera otter")
    otter_cli.add_arguments(parser)
    args = parser.parse_args(["--no-mcp", "-p", "hello"])
    assert args.no_mcp is True

    args2 = parser.parse_args(["-p", "hello"])
    assert args2.no_mcp is False


# ---------------------------------------------------------------------------
# _attach_mcp_tools: helper contract
# ---------------------------------------------------------------------------


def test_attach_mcp_tools_grows_list_when_servers_present(
    patch_mcp: dict[str, Any], tmp_path: Path
) -> None:
    """Helper appends one tool per enabled server and calls ``connect_all``."""
    patch_mcp["servers"] = [
        MCPServerConfig(name="fs", transport="stdio", command=["fs-server"]),
        MCPServerConfig(name="weather", transport="http", url="https://example/mcp"),
    ]
    base = [_FakeTool("read"), _FakeTool("write")]

    augmented = otter_cli._attach_mcp_tools(list(base), project_root=tmp_path)

    # Two MCP tools appended (one per server).
    assert len(augmented) == len(base) + 2
    augmented_names = [t.name for t in augmented]
    assert "mcp.fs.echo" in augmented_names
    assert "mcp.weather.echo" in augmented_names

    # The fake client recorded both registrations and one connect.
    client = patch_mcp["last_client"]
    assert client is not None
    assert {n for n, _ in client.added} == {"fs", "weather"}
    assert client.connect_calls == 1


def test_attach_mcp_tools_empty_servers_returns_unchanged(
    patch_mcp: dict[str, Any], tmp_path: Path
) -> None:
    """No servers means the helper short-circuits before importing MCPClient."""
    patch_mcp["servers"] = []
    base = [_FakeTool("read")]

    out = otter_cli._attach_mcp_tools(list(base), project_root=tmp_path)
    assert [t.name for t in out] == ["read"]
    # MCPClient must not have been constructed.
    assert patch_mcp["last_client"] is None


def test_attach_mcp_tools_disabled_servers_skipped(
    patch_mcp: dict[str, Any], tmp_path: Path
) -> None:
    """``enabled=False`` entries don't reach ``add_from_spec``."""
    patch_mcp["servers"] = [
        MCPServerConfig(
            name="off", transport="stdio", command=["off"], enabled=False,
        ),
        MCPServerConfig(name="on", transport="stdio", command=["on-server"]),
    ]
    base = [_FakeTool("read")]
    out = otter_cli._attach_mcp_tools(list(base), project_root=tmp_path)

    client = patch_mcp["last_client"]
    assert client is not None
    assert [n for n, _ in client.added] == ["on"]
    assert len(out) == len(base) + 1


def test_attach_mcp_tools_per_server_register_failure_keeps_others(
    patch_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """``add_from_spec`` failure on one server doesn't drop the others."""

    class _PickyClient(_FakeMCPClient):
        def add_from_spec(self, name: str, spec: dict[str, Any]) -> None:
            if name == "broken":
                raise ValueError("bad spec")
            super().add_from_spec(name, spec)

    patch_mcp["client_factory"] = _PickyClient
    patch_mcp["servers"] = [
        MCPServerConfig(name="ok", transport="stdio", command=["ok-server"]),
        MCPServerConfig(name="broken", transport="stdio", command=["nope"]),
    ]
    base: list[Any] = []
    out = otter_cli._attach_mcp_tools(list(base), project_root=tmp_path)

    client = patch_mcp["last_client"]
    assert client is not None
    assert [n for n, _ in client.added] == ["ok"]
    assert client.connect_calls == 1
    assert len(out) == 1
    assert out[0].name == "mcp.ok.echo"


def test_attach_mcp_tools_connect_failure_returns_unchanged(
    patch_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """When ``connect_all`` raises we keep the original tools list."""

    class _BrokenClient(_FakeMCPClient):
        def connect_all(self) -> None:
            raise ConnectionError("server down")

    patch_mcp["client_factory"] = _BrokenClient
    patch_mcp["servers"] = [
        MCPServerConfig(name="fs", transport="stdio", command=["fs-server"]),
    ]
    base = [_FakeTool("read"), _FakeTool("write")]
    out = otter_cli._attach_mcp_tools(list(base), project_root=tmp_path)

    assert [t.name for t in out] == ["read", "write"]


def test_attach_mcp_tools_loader_failure_returns_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Discovery exceptions never propagate out of the helper."""

    def _boom(_root: Path) -> list[MCPServerConfig]:
        raise RuntimeError("disk gone")

    monkeypatch.setattr("chimera.otter.mcp.load_mcp_servers", _boom)
    base = [_FakeTool("read")]
    out = otter_cli._attach_mcp_tools(list(base), project_root=tmp_path)
    assert [t.name for t in out] == ["read"]


# ---------------------------------------------------------------------------
# --no-mcp short-circuits at the assembly sites
# ---------------------------------------------------------------------------


def test_print_mode_no_mcp_short_circuits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``--no-mcp`` on the print path skips ``_attach_mcp_tools`` entirely."""
    calls: list[Path] = []

    def _spy(tools: list[Any], project_root: Path) -> list[Any]:
        calls.append(project_root)
        return list(tools) + [_FakeTool("mcp.spy.echo")]

    monkeypatch.setattr(otter_cli, "_attach_mcp_tools", _spy)

    # Stub everything else _run_print_mode pulls in so the test stays unit-y.
    class _StubProvider:
        model_name = "stub"

    monkeypatch.setattr(otter_cli, "_build_provider", lambda _m: _StubProvider())

    class _StubEnv:
        def __init__(self, workdir: str) -> None:
            self.workdir = workdir

        def setup(self) -> None:
            pass

        def cleanup(self) -> None:
            pass

    monkeypatch.setattr("chimera.env.local.LocalEnvironment", _StubEnv)

    captured: dict[str, Any] = {}

    class _StubAgent:
        def __init__(self, *, provider: Any, tools: list[Any],
                     loop: Any, prompt: Any) -> None:
            captured["tools"] = tools

        async def async_run(self, *_a: Any, **_kw: Any) -> Any:
            class _R:
                output = ""
                steps = 0
                cost = 0.0
                tool_calls_total = 0
                success = True
                error = None
            return _R()

    monkeypatch.setattr("chimera.core.agent.Agent", _StubAgent)

    args = argparse.Namespace(
        model="stub",
        cwd=str(tmp_path),
        max_steps=1,
        output_format="text",
        no_color=True,
        no_rich=True,
        no_save=True,
        no_lsp=True,
        no_rules=True,
        no_mcp=True,  # the flag under test
        allowed_tools="",
        print_mode="hello",
        run_id=None,
    )
    rc = otter_cli._run_print_mode(args)
    assert rc in (0, 1)  # success path; either is fine — only wiring matters.
    assert calls == [], "_attach_mcp_tools must not be called when --no-mcp is set"
    # Tools passed to the agent are the base tools plus LSP (skipped here);
    # crucially, no spy tool was appended.
    assert all(getattr(t, "name", "") != "mcp.spy.echo" for t in captured["tools"])


def test_print_mode_default_calls_attach_mcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Default (``no_mcp=False``) wires MCP tools into the agent's tool list."""
    calls: list[Path] = []

    def _spy(tools: list[Any], project_root: Path) -> list[Any]:
        calls.append(project_root)
        return list(tools) + [_FakeTool("mcp.spy.echo")]

    monkeypatch.setattr(otter_cli, "_attach_mcp_tools", _spy)

    class _StubProvider:
        model_name = "stub"

    monkeypatch.setattr(otter_cli, "_build_provider", lambda _m: _StubProvider())

    class _StubEnv:
        def __init__(self, workdir: str) -> None:
            self.workdir = workdir

        def setup(self) -> None:
            pass

        def cleanup(self) -> None:
            pass

    monkeypatch.setattr("chimera.env.local.LocalEnvironment", _StubEnv)

    captured: dict[str, Any] = {}

    class _StubAgent:
        def __init__(self, *, provider: Any, tools: list[Any],
                     loop: Any, prompt: Any) -> None:
            captured["tools"] = tools

        async def async_run(self, *_a: Any, **_kw: Any) -> Any:
            class _R:
                output = ""
                steps = 0
                cost = 0.0
                tool_calls_total = 0
                success = True
                error = None
            return _R()

    monkeypatch.setattr("chimera.core.agent.Agent", _StubAgent)

    args = argparse.Namespace(
        model="stub",
        cwd=str(tmp_path),
        max_steps=1,
        output_format="text",
        no_color=True,
        no_rich=True,
        no_save=True,
        no_lsp=True,
        no_rules=True,
        no_mcp=False,  # default: MCP on
        allowed_tools="",
        print_mode="hello",
        run_id=None,
    )
    rc = otter_cli._run_print_mode(args)
    assert rc in (0, 1)
    assert len(calls) == 1
    assert calls[0] == Path(tmp_path)
    # Tools passed to the agent include the spy tool we appended.
    assert any(getattr(t, "name", "") == "mcp.spy.echo" for t in captured["tools"])


def test_repl_build_otter_agent_attaches_mcp_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``build_otter_agent`` calls ``_attach_mcp_tools`` unless ``--no-mcp``."""
    from chimera.otter import repl as otter_repl

    calls: list[Path] = []

    def _spy(tools: list[Any], project_root: Path) -> list[Any]:
        calls.append(project_root)
        return list(tools) + [_FakeTool("mcp.spy.echo")]

    monkeypatch.setattr(otter_cli, "_attach_mcp_tools", _spy)

    class _StubProvider:
        model_name = "stub"

    args = argparse.Namespace(
        model="stub",
        cwd=str(tmp_path),
        max_steps=1,
        no_lsp=True,
        no_rules=True,
        no_mcp=False,
    )
    agent = otter_repl.build_otter_agent(args, provider=_StubProvider())
    assert agent is not None
    assert len(calls) == 1
    assert calls[0] == Path(str(tmp_path))


def test_repl_build_otter_agent_no_mcp_short_circuits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``build_otter_agent`` skips ``_attach_mcp_tools`` when ``--no-mcp`` is set."""
    from chimera.otter import repl as otter_repl

    calls: list[Path] = []

    def _spy(tools: list[Any], project_root: Path) -> list[Any]:
        calls.append(project_root)
        return list(tools)

    monkeypatch.setattr(otter_cli, "_attach_mcp_tools", _spy)

    class _StubProvider:
        model_name = "stub"

    args = argparse.Namespace(
        model="stub",
        cwd=str(tmp_path),
        max_steps=1,
        no_lsp=True,
        no_rules=True,
        no_mcp=True,
    )
    agent = otter_repl.build_otter_agent(args, provider=_StubProvider())
    assert agent is not None
    assert calls == []

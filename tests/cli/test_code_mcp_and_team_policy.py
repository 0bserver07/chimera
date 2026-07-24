"""``chimera code -p`` loads MCP tools and honours a team policy.

Both contracts were found broken by trying to run what the agent-teams
guide describes (#151):

* MCP servers were loaded only on the legacy ReAct path, so the
  assembled stack behind ``-p`` — the documented way to run an internal
  Chimera teammate — had no ``team_*`` tools while its prompt told it to
  call them.
* A teammate spawned under a team policy needs that posture to become a
  real gate, not a suggestion.

These tests stub the provider away: what is asserted is what
``CodingAgent`` was constructed with, and that a broken or absent config
degrades quietly rather than taking the run down.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from chimera.cli.code import load_mcp_tools, run_code


def _print_args(workdir: str, **overrides: Any) -> argparse.Namespace:
    base = dict(
        mode="interactive",
        model="test-model",
        workdir=workdir,
        max_steps=10,
        models="",
        preset=None,
        print_mode="do the thing",
        legacy_react=False,
        tui=False,
        list_cohorts=False,
        max_turns=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestLoadMcpTools:
    def test_no_config_means_no_tools(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        assert load_mcp_tools(str(tmp_path)) == []

    def test_project_config_is_read(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / ".mcp.json").write_text(json.dumps({
            "mcpServers": {"demo": {"command": "demo-server"}},
        }))

        seen: dict[str, Any] = {}

        class _Source:
            @staticmethod
            def from_config(config: dict[str, Any]) -> tuple[object, list[str]]:
                seen.update(config)
                return object(), ["tool-a", "tool-b"]

        monkeypatch.setattr("chimera.mcp.tools.MCPToolSource", _Source)

        assert load_mcp_tools(str(tmp_path)) == ["tool-a", "tool-b"]
        assert "demo" in seen["servers"]

    def test_a_broken_config_degrades_quietly(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        (tmp_path / ".mcp.json").write_text("{not json")

        assert load_mcp_tools(str(tmp_path)) == []
        assert "could not parse" in capsys.readouterr().out


class TestPrintModeWiring:
    @pytest.fixture
    def captured(self, monkeypatch: Any) -> dict[str, Any]:
        """Capture the kwargs ``-p`` mode builds its CodingAgent with."""
        captured: dict[str, Any] = {}

        class _FakeAgent:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

            async def run(self, task: str):  # noqa: ANN202 - async generator
                if False:  # pragma: no cover - never yields
                    yield None

        monkeypatch.setattr(
            "chimera.assembly.coding_agent.CodingAgent", _FakeAgent,
        )
        return captured

    def test_mcp_tools_reach_the_assembled_agent(
        self, tmp_path: Path, monkeypatch: Any, captured: dict[str, Any],
    ) -> None:
        monkeypatch.setattr(
            "chimera.cli.code.load_mcp_tools", lambda workdir: ["team-tool"],
        )

        assert run_code(_print_args(str(tmp_path))) == 0
        assert captured["extra_tools"] == ["team-tool"]

    def test_no_mcp_config_passes_none(
        self, tmp_path: Path, monkeypatch: Any, captured: dict[str, Any],
    ) -> None:
        monkeypatch.setattr("chimera.cli.code.load_mcp_tools", lambda workdir: [])

        assert run_code(_print_args(str(tmp_path))) == 0
        assert captured["extra_tools"] is None

    def test_no_team_policy_means_no_interceptors(
        self, tmp_path: Path, monkeypatch: Any, captured: dict[str, Any],
    ) -> None:
        # The unchanged-by-default guarantee, at the CLI seam.
        monkeypatch.delenv("CHIMERA_TEAM_POLICY", raising=False)
        monkeypatch.setattr("chimera.cli.code.load_mcp_tools", lambda workdir: [])

        assert run_code(_print_args(str(tmp_path))) == 0
        assert captured["interceptors"] is None

    def test_team_policy_becomes_a_tool_call_gate(
        self, tmp_path: Path, monkeypatch: Any, captured: dict[str, Any],
    ) -> None:
        monkeypatch.setenv("CHIMERA_TEAMS_HOME", str(tmp_path / "teams"))
        monkeypatch.setenv("CHIMERA_TEAM_POLICY", "read-only")
        monkeypatch.setattr("chimera.cli.code.load_mcp_tools", lambda workdir: [])

        assert run_code(_print_args(str(tmp_path))) == 0
        interceptors = captured["interceptors"]
        assert interceptors is not None
        assert len(interceptors.tool_call) == 1

    def test_an_unparseable_policy_refuses_the_run(
        self, tmp_path: Path, monkeypatch: Any, capsys: Any,
    ) -> None:
        # Fail closed: better to refuse than to work at permissions
        # nobody chose.
        monkeypatch.setenv("CHIMERA_TEAM_POLICY", "sorta-safe")

        assert run_code(_print_args(str(tmp_path))) == 2
        assert "team policy error" in capsys.readouterr().err

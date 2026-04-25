"""Tests for MCP ``mcp__server__tool`` naming and 3-scope config merge."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.mcp import (
    MCPClient,
    load_mcp_config,
    mcp_normalize,
    mcp_prefix,
    mcp_unprefix,
)
from chimera.mcp.transport import MCPTransport
from chimera.permissions.rules import PermissionRuleValue


class StubTransport(MCPTransport):
    """In-memory transport that scripts a fixed sequence of replies."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def start(self) -> None:
        return None

    def send(self, message: dict) -> dict | None:
        self.sent.append(message)
        method = message.get("method")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": {}}
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {
                    "tools": [
                        {
                            "name": "read_file",
                            "description": "Read a file.",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ],
                },
            }
        if method == "tools/call":
            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }
        return None

    def close(self) -> None:
        return None


def test_normalize_and_unprefix() -> None:
    assert mcp_normalize("FileSystem.v2") == "filesystem_v2"
    assert mcp_prefix("FileSystem", "Read File") == "mcp__filesystem__read_file"
    assert mcp_unprefix("mcp__filesystem__read_file") == ("filesystem", "read_file")
    assert mcp_unprefix("not_mcp") == ("", "not_mcp")


def test_discovered_tool_is_prefixed() -> None:
    client = MCPClient()
    transport = StubTransport()
    client.add_transport("filesystem", transport)
    client.connect_all()

    tools = client.tools
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "mcp__filesystem__read_file"
    # Original upstream name must be preserved for dispatch back to server.
    assert getattr(tool, "original_name") == "read_file"

    # Round-trip a call and confirm we send the *original* name to the server.
    result = tool.execute({}, env=None)
    assert result.success
    sent_call = [m for m in transport.sent if m.get("method") == "tools/call"][-1]
    assert sent_call["params"]["name"] == "read_file"


def test_server_level_permission_matches_prefixed_tool() -> None:
    matcher = PermissionRuleValue(tool_name="mcp__filesystem")
    assert matcher.matches("mcp__filesystem__read_file", input_content=None) is True
    assert matcher.matches("mcp__other__read_file", input_content=None) is False


def test_load_mcp_config_three_scopes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    (home / ".claude").mkdir(parents=True)
    (cwd / ".claude").mkdir(parents=True)

    user_cfg = {"mcpServers": {
        "shared": {"command": "user-cmd"},
        "user-only": {"command": "u"},
    }}
    proj_cfg = {"mcpServers": {
        "shared": {"command": "project-cmd"},
        "proj-only": {"command": "p"},
    }}
    local_cfg = {"mcpServers": {
        "shared": {"command": "local-cmd"},
        "local-only": {"command": "l"},
    }}
    (home / ".claude" / ".mcp.json").write_text(json.dumps(user_cfg))
    (cwd / ".claude" / ".mcp.json").write_text(json.dumps(proj_cfg))
    (cwd / ".claude" / ".mcp.local.json").write_text(json.dumps(local_cfg))

    merged = load_mcp_config(cwd=cwd, home=home)
    servers = merged["servers"]
    # Local wins over project wins over user.
    assert servers["shared"]["command"] == "local-cmd"
    assert "user-only" in servers
    assert "proj-only" in servers
    assert "local-only" in servers


def test_load_mcp_config_partial_scopes(tmp_path: Path) -> None:
    # Only user scope present -- still works.
    home = tmp_path / "home"
    cwd = tmp_path / "proj"
    cwd.mkdir()
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".mcp.json").write_text(json.dumps({
        "mcpServers": {"alone": {"command": "x"}}
    }))
    merged = load_mcp_config(cwd=cwd, home=home)
    assert "alone" in merged["servers"]


def test_load_mcp_config_handles_missing_files(tmp_path: Path) -> None:
    merged = load_mcp_config(cwd=tmp_path, home=tmp_path)
    assert merged == {"servers": {}}


@pytest.fixture(autouse=True)
def _clean_active_clients() -> None:
    # Stub transports leak nothing system-wide, but the module-level
    # _ACTIVE_CLIENTS list grows in some flows; defensively clear after each test.
    from chimera.mcp import tools as mcp_tools_mod
    yield
    mcp_tools_mod._ACTIVE_CLIENTS.clear()

"""Tests for ``chimera.otter.mcp.load_mcp_servers``.

Fixture-style: write fake config JSON into ``tmp_path``, point ``HOME`` at
it, and assert the merged :class:`MCPServerConfig` list. We never touch
the real user home.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.otter.mcp import MCPServerConfig, load_mcp_servers


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated fake HOME and re-root ``Path.home`` + ``$HOME`` at it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Provide an empty project directory under tmp_path."""
    proj = tmp_path / "project"
    proj.mkdir()
    return proj


def _write_user_config(home: Path, payload: dict[str, Any]) -> Path:
    cfg_dir = home / ".opencode"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(json.dumps(payload), encoding="utf-8")
    return cfg_file


def _write_project_mcp(
    project: Path, payload: dict[str, Any], *, name: str = "mcp.json"
) -> Path:
    cfg_dir = project / ".opencode"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / name
    cfg_file.write_text(json.dumps(payload), encoding="utf-8")
    return cfg_file


# ---------------------------------------------------------------------------
# Empty / missing inputs
# ---------------------------------------------------------------------------


def test_no_config_files_returns_empty(fake_home: Path, project: Path) -> None:
    assert load_mcp_servers(project) == []


def test_missing_mcp_block_returns_empty(fake_home: Path, project: Path) -> None:
    _write_user_config(fake_home, {"model": "stub"})
    _write_project_mcp(project, {})
    assert load_mcp_servers(project) == []


def test_malformed_user_config_is_ignored(fake_home: Path, project: Path) -> None:
    (fake_home / ".opencode").mkdir()
    (fake_home / ".opencode" / "config.json").write_text("{not json", encoding="utf-8")
    _write_project_mcp(
        project,
        {"mcp": {"fs": {"type": "local", "command": ["fs-server"]}}},
    )
    cfgs = load_mcp_servers(project)
    assert [c.name for c in cfgs] == ["fs"]


# ---------------------------------------------------------------------------
# stdio (upstream "local") transport
# ---------------------------------------------------------------------------


def test_user_stdio_local_type_loads(fake_home: Path, project: Path) -> None:
    _write_user_config(
        fake_home,
        {
            "mcp": {
                "fs": {
                    "type": "local",
                    "command": ["fs-server", "--root", "/tmp"],
                    "environment": {"LOG": "1"},
                    "enabled": True,
                    "timeout": 5000,
                },
            },
        },
    )
    cfgs = load_mcp_servers(project)
    assert len(cfgs) == 1
    fs = cfgs[0]
    assert fs == MCPServerConfig(
        name="fs",
        transport="stdio",
        command=["fs-server", "--root", "/tmp"],
        env={"LOG": "1"},
        enabled=True,
        timeout_ms=5000,
    )


def test_legacy_chimera_env_key_still_works(fake_home: Path, project: Path) -> None:
    """A bare ``.opencode/mcp.json`` may use the chimera ``env`` key."""
    _write_project_mcp(
        project,
        {
            "mcp": {
                "tools": {
                    "command": ["tools-bin"],
                    "env": {"FOO": "bar"},
                },
            },
        },
    )
    cfgs = load_mcp_servers(project)
    assert cfgs[0].env == {"FOO": "bar"}
    assert cfgs[0].transport == "stdio"


def test_inferred_transport_from_command(fake_home: Path, project: Path) -> None:
    _write_user_config(
        fake_home,
        {"mcp": {"raw": {"command": ["raw-server"]}}},
    )
    cfgs = load_mcp_servers(project)
    assert cfgs[0].transport == "stdio"
    assert cfgs[0].command == ["raw-server"]


# ---------------------------------------------------------------------------
# http (upstream "remote") transport
# ---------------------------------------------------------------------------


def test_user_http_remote_type_loads(fake_home: Path, project: Path) -> None:
    _write_user_config(
        fake_home,
        {
            "mcp": {
                "weather": {
                    "type": "remote",
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer abc"},
                },
            },
        },
    )
    cfgs = load_mcp_servers(project)
    assert len(cfgs) == 1
    w = cfgs[0]
    assert w.transport == "http"
    assert w.url == "https://example.com/mcp"
    assert w.headers == {"Authorization": "Bearer abc"}
    assert w.enabled is True


def test_inferred_http_from_url(fake_home: Path, project: Path) -> None:
    _write_project_mcp(
        project, {"mcp": {"r": {"url": "https://x/mcp"}}}
    )
    cfgs = load_mcp_servers(project)
    assert cfgs[0].transport == "http"


# ---------------------------------------------------------------------------
# Project overrides user (merge semantics)
# ---------------------------------------------------------------------------


def test_project_overrides_user_on_name_conflict(
    fake_home: Path, project: Path
) -> None:
    _write_user_config(
        fake_home,
        {
            "mcp": {
                "fs": {"type": "local", "command": ["user-fs"]},
                "shared": {"type": "remote", "url": "https://user/shared"},
            },
        },
    )
    _write_project_mcp(
        project,
        {
            "mcp": {
                "fs": {"type": "local", "command": ["proj-fs"]},
                "extra": {"type": "local", "command": ["proj-extra"]},
            },
        },
    )

    cfgs = {c.name: c for c in load_mcp_servers(project)}
    assert set(cfgs) == {"fs", "shared", "extra"}
    assert cfgs["fs"].command == ["proj-fs"]            # project wins
    assert cfgs["shared"].url == "https://user/shared"  # user-only entry survives
    assert cfgs["extra"].command == ["proj-extra"]


def test_project_mcp_json_takes_precedence_over_project_config_json(
    fake_home: Path, project: Path
) -> None:
    """When both ``.opencode/mcp.json`` and ``.opencode/config.json`` define
    MCP servers in the project, ``mcp.json`` wins (it's the dedicated file)."""
    _write_project_mcp(
        project,
        {"mcp": {"fs": {"type": "local", "command": ["from-mcp-json"]}}},
        name="mcp.json",
    )
    _write_project_mcp(
        project,
        {"mcp": {"fs": {"type": "local", "command": ["from-config-json"]}}},
        name="config.json",
    )
    cfgs = load_mcp_servers(project)
    assert len(cfgs) == 1
    assert cfgs[0].command == ["from-mcp-json"]


def test_project_config_json_used_when_no_mcp_json(
    fake_home: Path, project: Path
) -> None:
    _write_project_mcp(
        project,
        {"mcp": {"fs": {"type": "local", "command": ["from-config-json"]}}},
        name="config.json",
    )
    cfgs = load_mcp_servers(project)
    assert len(cfgs) == 1
    assert cfgs[0].command == ["from-config-json"]


# ---------------------------------------------------------------------------
# Bare-map shape (just ``{name: entry, ...}``)
# ---------------------------------------------------------------------------


def test_bare_map_in_project_mcp_json(fake_home: Path, project: Path) -> None:
    _write_project_mcp(
        project,
        {
            "fs": {"type": "local", "command": ["fs-server"]},
            "weather": {"type": "remote", "url": "https://x/mcp"},
        },
    )
    cfgs = load_mcp_servers(project)
    names = sorted(c.name for c in cfgs)
    assert names == ["fs", "weather"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_disabled_entry_is_returned_with_flag(fake_home: Path, project: Path) -> None:
    _write_user_config(
        fake_home,
        {
            "mcp": {
                "fs": {
                    "type": "local",
                    "command": ["fs"],
                    "enabled": False,
                },
            },
        },
    )
    cfgs = load_mcp_servers(project)
    assert len(cfgs) == 1
    assert cfgs[0].enabled is False


def test_unknown_entry_with_no_command_or_url_is_dropped(
    fake_home: Path, project: Path
) -> None:
    _write_user_config(
        fake_home,
        {"mcp": {"toggle": {"enabled": False}}},
    )
    assert load_mcp_servers(project) == []


def test_invalid_http_entry_with_blank_url_is_dropped(
    fake_home: Path, project: Path
) -> None:
    _write_user_config(
        fake_home,
        {"mcp": {"r": {"type": "remote", "url": "   "}}},
    )
    assert load_mcp_servers(project) == []


def test_invalid_stdio_entry_with_empty_command_is_dropped(
    fake_home: Path, project: Path
) -> None:
    _write_user_config(
        fake_home,
        {"mcp": {"s": {"type": "local", "command": []}}},
    )
    assert load_mcp_servers(project) == []


# ---------------------------------------------------------------------------
# to_client_spec round-trip
# ---------------------------------------------------------------------------


def test_to_client_spec_stdio() -> None:
    cfg = MCPServerConfig(
        name="fs",
        transport="stdio",
        command=["fs-server", "--root", "/tmp"],
        env={"LOG": "1"},
    )
    spec = cfg.to_client_spec()
    assert spec == {
        "transport": "stdio",
        "command": "fs-server",
        "args": ["--root", "/tmp"],
        "env": {"LOG": "1"},
    }


def test_to_client_spec_http() -> None:
    cfg = MCPServerConfig(
        name="w",
        transport="http",
        url="https://x/mcp",
        headers={"Authorization": "Bearer t"},
    )
    spec = cfg.to_client_spec()
    assert spec == {
        "transport": "http",
        "url": "https://x/mcp",
        "headers": {"Authorization": "Bearer t"},
    }


# ---------------------------------------------------------------------------
# OAuth round-trip (dataclass field + to_client_spec passthrough)
# ---------------------------------------------------------------------------


def test_oauth_block_round_trips_through_config_and_spec(
    fake_home: Path, project: Path
) -> None:
    """An ``oauth`` block in ``~/.opencode/config.json`` must survive the
    full pipeline: JSON -> ``MCPServerConfig.oauth`` -> ``to_client_spec``."""
    oauth_block = {
        "client_id": "cid-123",
        "auth_server_metadata_url": "https://issuer.example/.well-known/oauth",
        "redirect_uri": "http://127.0.0.1:7777/callback",
        "scopes": ["read", "write"],
    }
    _write_user_config(
        fake_home,
        {
            "mcp": {
                "secured": {
                    "type": "remote",
                    "url": "https://example.com/mcp",
                    "oauth": oauth_block,
                },
            },
        },
    )
    cfgs = load_mcp_servers(project)
    assert len(cfgs) == 1
    cfg = cfgs[0]
    assert cfg.transport == "http"
    assert cfg.oauth == oauth_block
    # Round-trip into the dict shape MCPClient.add_from_spec consumes.
    spec = cfg.to_client_spec()
    assert spec == {
        "transport": "http",
        "url": "https://example.com/mcp",
        "oauth": oauth_block,
    }


def test_oauth_block_with_headers_in_spec() -> None:
    """Both ``headers`` and ``oauth`` should appear in the spec when set."""
    cfg = MCPServerConfig(
        name="secured",
        transport="http",
        url="https://example.com/mcp",
        headers={"X-Trace": "1"},
        oauth={
            "client_id": "cid-456",
            "token_endpoint": "https://issuer.example/token",
            "authorization_endpoint": "https://issuer.example/authorize",
        },
    )
    spec = cfg.to_client_spec()
    assert spec == {
        "transport": "http",
        "url": "https://example.com/mcp",
        "headers": {"X-Trace": "1"},
        "oauth": {
            "client_id": "cid-456",
            "token_endpoint": "https://issuer.example/token",
            "authorization_endpoint": "https://issuer.example/authorize",
        },
    }


def test_oauth_omitted_when_absent() -> None:
    """Default ``oauth=None`` must NOT add an ``oauth`` key to the spec."""
    cfg = MCPServerConfig(
        name="plain",
        transport="http",
        url="https://x/mcp",
    )
    spec = cfg.to_client_spec()
    assert "oauth" not in spec
    assert spec == {"transport": "http", "url": "https://x/mcp"}


def test_oauth_ignored_for_stdio_spec() -> None:
    """``oauth`` is meaningful only for http; stdio specs must drop it."""
    cfg = MCPServerConfig(
        name="fs",
        transport="stdio",
        command=["fs-server"],
        oauth={"client_id": "ignored"},
    )
    spec = cfg.to_client_spec()
    assert "oauth" not in spec


def test_oauth_non_dict_in_config_is_ignored(
    fake_home: Path, project: Path
) -> None:
    """A malformed ``oauth`` value (not a dict) must be dropped, not raise."""
    _write_user_config(
        fake_home,
        {
            "mcp": {
                "r": {
                    "type": "remote",
                    "url": "https://x/mcp",
                    "oauth": "not-a-dict",
                },
            },
        },
    )
    cfgs = load_mcp_servers(project)
    assert len(cfgs) == 1
    assert cfgs[0].oauth is None


# ---------------------------------------------------------------------------
# explicit ``home=`` test seam
# ---------------------------------------------------------------------------


def test_explicit_home_kwarg_overrides_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing ``home=`` lets callers point the user-scope lookup anywhere."""
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    monkeypatch.setenv("HOME", str(real_home))
    # No file in real_home -> would yield empty.

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    _write_user_config(
        fake_home,
        {"mcp": {"fs": {"type": "local", "command": ["fs"]}}},
    )

    project = tmp_path / "project"
    project.mkdir()

    cfgs = load_mcp_servers(project, home=fake_home)
    assert [c.name for c in cfgs] == ["fs"]

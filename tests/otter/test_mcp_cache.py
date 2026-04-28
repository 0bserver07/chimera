"""Tests for the per-process MCP client cache (:mod:`chimera.otter.mcp_cache`).

The cache memoises one :class:`MCPClient` per ``(server_name, transport
signature)`` so repeated calls into
:func:`chimera.otter.cli._attach_mcp_tools` from a long-running process
(the otter HTTP/ACP serve loops build a fresh agent per session) reuse
the same connected client and never re-spawn the same stdio MCP server
subprocess.

These tests pin the contract:

    * Two calls with the same server set construct exactly one
      :class:`MCPClient`.
    * A config change (different command, env, or url/headers) invalidates
      the cache entry and forces a rebuild.
    * The atexit teardown calls ``disconnect_all`` exactly once per cached
      client, even when the same client backed multiple keys.
    * Fresh server names build a new client without disturbing existing
      cached ones.

The :mod:`chimera.otter.mcp_cache` test fixtures are auto-reset by the
otter ``conftest.py`` so each test starts from an empty cache.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.otter import cli as otter_cli
from chimera.otter import mcp_cache
from chimera.otter.mcp import MCPServerConfig


# ---------------------------------------------------------------------------
# Test doubles — match the surface used by the wave-1 wiring tests so the
# two suites stay readable side-by-side.
# ---------------------------------------------------------------------------


class _FakeTool:
    """Minimal stand-in for :class:`chimera.core.tool.BaseTool`."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeMCPClient:
    """Recordable MCPClient stand-in.

    Each instance tracks how many times :meth:`add_from_spec` was called,
    how many ``connect_all`` invocations it received, and surfaces a
    :attr:`tools` list the cache wiring zips back into the agent.
    :meth:`disconnect_all` is recorded so the atexit teardown test can
    verify single-shot invocation per live client.
    """

    instance_count = 0

    def __init__(self) -> None:
        type(self).instance_count += 1
        self.id_n: int = type(self).instance_count
        self.add_calls: list[tuple[str, dict[str, Any]]] = []
        self.connect_calls: int = 0
        self.disconnect_calls: int = 0
        self._tools: list[_FakeTool] = []

    def add_from_spec(self, name: str, spec: dict[str, Any]) -> None:
        self.add_calls.append((name, spec))

    def connect_all(self) -> None:
        self.connect_calls += 1
        self._tools = [_FakeTool(f"mcp.{n}.echo") for n, _ in self.add_calls]

    def disconnect_all(self) -> None:
        self.disconnect_calls += 1

    @property
    def tools(self) -> list[_FakeTool]:
        return list(self._tools)


@pytest.fixture(autouse=True)
def _reset_instance_counter() -> None:
    """Zero the global :class:`_FakeMCPClient` counter between cases."""
    _FakeMCPClient.instance_count = 0


@pytest.fixture
def patch_loader_and_client(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Wire fake :func:`load_mcp_servers` + :class:`MCPClient` into the cache.

    Returns a knob dict tests mutate:
        * ``servers``: list returned by ``load_mcp_servers``.
        * ``clients``: every :class:`_FakeMCPClient` constructed by the
          cache, in build order. Tests assert ``len(clients) == 1``
          across two ``_attach_mcp_tools`` calls.
    """
    state: dict[str, Any] = {"servers": [], "clients": []}

    def _fake_load(_root: Path) -> list[MCPServerConfig]:
        return list(state["servers"])

    def _fake_factory() -> Any:
        client = _FakeMCPClient()
        state["clients"].append(client)
        return client

    monkeypatch.setattr("chimera.otter.mcp.load_mcp_servers", _fake_load)
    # The cache imports MCPClient from chimera.mcp.client at call time;
    # patching the source module covers the lookup.
    monkeypatch.setattr("chimera.mcp.client.MCPClient", _fake_factory)
    return state


# ---------------------------------------------------------------------------
# Cache hit/miss contracts
# ---------------------------------------------------------------------------


def test_attach_mcp_tools_reuses_client_across_calls(
    patch_loader_and_client: dict[str, Any], tmp_path: Path,
) -> None:
    """Calling ``_attach_mcp_tools`` twice with the same set builds one client."""
    patch_loader_and_client["servers"] = [
        MCPServerConfig(name="fs", transport="stdio", command=["fs-server"]),
        MCPServerConfig(
            name="weather", transport="http", url="https://example/mcp",
        ),
    ]

    base = [_FakeTool("read")]
    out_first = otter_cli._attach_mcp_tools(list(base), project_root=tmp_path)
    out_second = otter_cli._attach_mcp_tools(list(base), project_root=tmp_path)

    clients = patch_loader_and_client["clients"]
    assert len(clients) == 1, (
        f"expected one MCPClient across two calls, got {len(clients)}"
    )
    client = clients[0]
    # add_from_spec is recorded once per server, only on the first build.
    assert {n for n, _ in client.add_calls} == {"fs", "weather"}
    assert client.connect_calls == 1
    # Both call sites returned the augmented list.
    assert any(t.name == "mcp.fs.echo" for t in out_first)
    assert any(t.name == "mcp.fs.echo" for t in out_second)


def test_attach_mcp_tools_rebuilds_on_spec_change(
    patch_loader_and_client: dict[str, Any], tmp_path: Path,
) -> None:
    """A config change (different command) invalidates the cache key."""
    patch_loader_and_client["servers"] = [
        MCPServerConfig(name="fs", transport="stdio", command=["fs-server-v1"]),
    ]
    otter_cli._attach_mcp_tools([], project_root=tmp_path)

    # Flip the command — the (name, signature) key now differs.
    patch_loader_and_client["servers"] = [
        MCPServerConfig(name="fs", transport="stdio", command=["fs-server-v2"]),
    ]
    otter_cli._attach_mcp_tools([], project_root=tmp_path)

    assert len(patch_loader_and_client["clients"]) == 2
    # Each client only saw the one spec it was built for.
    assert patch_loader_and_client["clients"][0].add_calls[0][1]["command"] == (
        "fs-server-v1"
    )
    assert patch_loader_and_client["clients"][1].add_calls[0][1]["command"] == (
        "fs-server-v2"
    )


def test_attach_mcp_tools_rebuilds_when_server_set_grows(
    patch_loader_and_client: dict[str, Any], tmp_path: Path,
) -> None:
    """Adding a new server forces a fresh client (not all keys cached)."""
    patch_loader_and_client["servers"] = [
        MCPServerConfig(name="fs", transport="stdio", command=["fs"]),
    ]
    otter_cli._attach_mcp_tools([], project_root=tmp_path)

    patch_loader_and_client["servers"] = [
        MCPServerConfig(name="fs", transport="stdio", command=["fs"]),
        MCPServerConfig(name="weather", transport="http", url="https://x/mcp"),
    ]
    otter_cli._attach_mcp_tools([], project_root=tmp_path)

    # Two clients: the second call's "weather" key is a miss so the whole
    # set rebuilds. The original "fs" client still lives in the cache.
    assert len(patch_loader_and_client["clients"]) == 2


def test_cache_key_is_dict_order_invariant() -> None:
    """``cache_key`` produces the same value regardless of dict insertion order."""
    a = mcp_cache.cache_key("fs", {"transport": "stdio", "command": "x", "args": ["-y"]})
    b = mcp_cache.cache_key("fs", {"args": ["-y"], "command": "x", "transport": "stdio"})
    assert a == b


def test_cache_key_changes_with_env() -> None:
    """A new env entry invalidates the cache (subprocesses see different state)."""
    a = mcp_cache.cache_key("fs", {"transport": "stdio", "command": "x"})
    b = mcp_cache.cache_key(
        "fs", {"transport": "stdio", "command": "x", "env": {"TOKEN": "1"}},
    )
    assert a != b


# ---------------------------------------------------------------------------
# Teardown / shutdown
# ---------------------------------------------------------------------------


def test_shutdown_disconnects_each_client_once(
    patch_loader_and_client: dict[str, Any], tmp_path: Path,
) -> None:
    """``shutdown_cached_clients`` calls disconnect_all once per live client."""
    patch_loader_and_client["servers"] = [
        MCPServerConfig(name="fs", transport="stdio", command=["fs"]),
        MCPServerConfig(name="weather", transport="http", url="https://x/mcp"),
    ]
    otter_cli._attach_mcp_tools([], project_root=tmp_path)
    client = patch_loader_and_client["clients"][0]
    assert client.disconnect_calls == 0

    mcp_cache.shutdown_cached_clients()
    assert client.disconnect_calls == 1, (
        "shutdown should disconnect each cached client exactly once"
    )

    # Idempotent — second call after the cache cleared is a no-op.
    mcp_cache.shutdown_cached_clients()
    assert client.disconnect_calls == 1


def test_shutdown_swallows_disconnect_errors(
    patch_loader_and_client: dict[str, Any], tmp_path: Path,
) -> None:
    """A misbehaving ``disconnect_all`` never propagates out of the teardown."""

    class _BadClient(_FakeMCPClient):
        def disconnect_all(self) -> None:
            super().disconnect_all()
            raise OSError("pipe closed")

    state = patch_loader_and_client

    def _bad_factory() -> Any:
        client = _BadClient()
        state["clients"].append(client)
        return client

    # Re-patch the constructor for this test only.
    import chimera.mcp.client as mcp_client_mod

    original = mcp_client_mod.MCPClient
    mcp_client_mod.MCPClient = _bad_factory  # type: ignore[misc,assignment]
    try:
        state["servers"] = [
            MCPServerConfig(name="fs", transport="stdio", command=["fs"]),
        ]
        otter_cli._attach_mcp_tools([], project_root=tmp_path)
        # Must not raise.
        mcp_cache.shutdown_cached_clients()
    finally:
        mcp_client_mod.MCPClient = original  # type: ignore[misc,assignment]


def test_atexit_hook_registered_on_first_insert(
    patch_loader_and_client: dict[str, Any], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache populates ``atexit`` on first miss (and not on every miss)."""
    registered: list[Any] = []

    def _fake_register(fn: Any) -> Any:
        registered.append(fn)
        return fn

    monkeypatch.setattr("chimera.otter.mcp_cache.atexit.register", _fake_register)
    # Re-arm the module-level guard so the fresh patch sees the hook.
    monkeypatch.setattr("chimera.otter.mcp_cache._ATEXIT_REGISTERED", False)

    patch_loader_and_client["servers"] = [
        MCPServerConfig(name="fs", transport="stdio", command=["fs"]),
    ]
    otter_cli._attach_mcp_tools([], project_root=tmp_path)
    otter_cli._attach_mcp_tools([], project_root=tmp_path)

    # First insert registers; second insert is idempotent.
    assert registered == [mcp_cache.shutdown_cached_clients]


# ---------------------------------------------------------------------------
# Direct ``get_or_create`` contract
# ---------------------------------------------------------------------------


def test_get_or_create_returns_none_for_empty_entries() -> None:
    """No entries => no client construction => ``None`` returned."""
    assert mcp_cache.get_or_create([]) is None


def test_get_or_create_returns_none_when_every_register_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every ``add_from_spec`` raising means no client to memoise."""

    class _RejectAll(_FakeMCPClient):
        def add_from_spec(self, name: str, spec: dict[str, Any]) -> None:
            raise ValueError("nope")

    monkeypatch.setattr("chimera.mcp.client.MCPClient", _RejectAll)
    out = mcp_cache.get_or_create(
        [("fs", {"transport": "stdio", "command": "fs"})],
    )
    assert out is None


def test_get_or_create_handles_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``connect_all`` failure => return ``None`` and skip memoisation."""

    class _BrokenConnect(_FakeMCPClient):
        def connect_all(self) -> None:
            raise ConnectionError("peer down")

    monkeypatch.setattr("chimera.mcp.client.MCPClient", _BrokenConnect)
    out = mcp_cache.get_or_create(
        [("fs", {"transport": "stdio", "command": "fs"})],
    )
    assert out is None
    # Cache must remain empty so a follow-up call retries cleanly.
    assert mcp_cache._MCP_CLIENT_CACHE == {}

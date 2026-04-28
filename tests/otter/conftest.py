"""Shared pytest fixtures for the otter test corpus.

The wave-3 :mod:`chimera.otter.mcp_cache` retains :class:`MCPClient`
instances across calls to :func:`chimera.otter.cli._attach_mcp_tools` so
repeated agent builds in a long-running process (HTTP/ACP serve) reuse
the already-spawned MCP subprocess.

Tests in :mod:`tests.otter.test_mcp_wiring` and
:mod:`tests.otter.test_mcp_cache` install fake clients via
``monkeypatch.setattr("chimera.mcp.client.MCPClient", _fake_factory)``
and expect each test case to start with an empty cache; without the
reset below, a hit from a prior test's spec would short-circuit the
second test's fake factory and break the assertions.

Resetting via :func:`chimera.otter.mcp_cache._reset_for_tests` (rather
than ``shutdown_cached_clients``) avoids calling ``disconnect_all`` on
fakes that don't implement it.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_otter_mcp_cache() -> Iterator[None]:
    """Drop any cached MCP clients before and after each otter test."""
    from chimera.otter import mcp_cache

    mcp_cache._reset_for_tests()
    yield
    mcp_cache._reset_for_tests()

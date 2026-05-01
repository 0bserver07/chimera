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

The wave-7 G1 fixture below additionally snapshots and restores the
slash-command shared registries. The otter and CLI slash modules
maintain module-level dicts (``_UNDO_STATES`` keyed by ``id(session)``,
``_COMMAND_ORIGINS`` / ``_COMMAND_HELP`` for help rendering, and
``_REGISTRY`` / ``COMMAND_NAMES`` for tab completion) which are
mutated in place by F6/F7/F8. ``id(session)`` recycling across tests
plus pollution from earlier tests in the suite (e.g. tests that
register custom or plugin commands) can leak into a later test's
view of the registry, even though every individual test passes when
run in isolation.

Snapshot + restore is preferred over targeted clearing so we don't
need to track which specific keys each test mutates: we simply put the
registry back exactly as we found it.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _reset_otter_mcp_cache() -> Iterator[None]:
    """Drop any cached MCP clients before and after each otter test."""
    from chimera.otter import mcp_cache

    mcp_cache._reset_for_tests()
    yield
    mcp_cache._reset_for_tests()


@pytest.fixture(autouse=True)
def _isolate_slash_registries() -> Iterator[None]:
    """Snapshot + restore the slash-command shared registries per test.

    Protects every otter test from cross-test pollution in the four
    module-level dicts/lists touched by F6/F7/F8:

    * ``chimera.otter.slash._UNDO_STATES`` — per-session undo/redo state
      keyed by ``id(session)``. Python recycles ``id`` values after a
      ``_FakeSession`` is garbage-collected, so the next test using the
      same id can inherit a non-empty undo stack and break assertions
      like ``test_undo_with_empty_stack_prints_friendly_notice``.
    * ``chimera.otter.slash._COMMAND_ORIGINS`` and ``_COMMAND_HELP`` —
      origin/help caches mutated by ``register_otter_slash`` /
      ``register_custom_commands`` / ``register_plugin_commands``.
    * ``chimera.cli.slash_commands._REGISTRY`` and
      ``chimera.cli.slash_commands.COMMAND_NAMES`` — the canonical
      handler registry and tab-completion view, both mutated in place
      so that prior ``from ... import COMMAND_NAMES`` callers observe
      live updates.
    """
    from chimera.cli import slash_commands as _cli_slash
    from chimera.otter import slash as _otter_slash

    saved_undo: dict[int, Any] = dict(_otter_slash._UNDO_STATES)
    saved_origins: dict[str, str] = dict(_otter_slash._COMMAND_ORIGINS)
    saved_help: dict[str, str] = dict(_otter_slash._COMMAND_HELP)
    saved_registry: dict[str, Any] = dict(_cli_slash._REGISTRY)
    saved_names: list[str] = list(_cli_slash.COMMAND_NAMES)

    try:
        yield
    finally:
        # Mutate in place so any callers holding a direct reference to
        # the dict/list object (the whole reason F7 mutates rather than
        # rebinds) see the restored contents.
        _otter_slash._UNDO_STATES.clear()
        _otter_slash._UNDO_STATES.update(saved_undo)
        _otter_slash._COMMAND_ORIGINS.clear()
        _otter_slash._COMMAND_ORIGINS.update(saved_origins)
        _otter_slash._COMMAND_HELP.clear()
        _otter_slash._COMMAND_HELP.update(saved_help)
        _cli_slash._REGISTRY.clear()
        _cli_slash._REGISTRY.update(saved_registry)
        _cli_slash.COMMAND_NAMES[:] = saved_names

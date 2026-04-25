"""Otter — Chimera coding-agent CLI in the open-source agent tradition.

Otter is to the open-source coding agent what mink is to its own upstream:
a Chimera subcommand composed of existing primitives (`AgentLoop`,
`LoopConfig`, `EventSourcedSession`, `PermissionChecker`, MCP/LSP/ACP) that
mirrors a server-plus-TUI ergonomic. Default backend: provider-agnostic
via :func:`chimera.providers.factory.create_provider`; server mode
(``chimera otter serve``) will expose the agent over HTTP for IDE / TUI /
ACP clients.

This module is built up by waves of focused subagents; see
``research/otter/`` for the per-agent reports and the design spec.
"""

from __future__ import annotations

from chimera.otter.cli import add_arguments, run

__all__: list[str] = [
    "add_arguments",
    "run",
]

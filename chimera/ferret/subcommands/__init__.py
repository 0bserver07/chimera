"""Codex-style subcommands for ``chimera ferret`` (W14-1).

The W14-1 spec adds five top-level subcommands plus three MCP-management
sub-actions to ferret, paralleling the surface the upstream IDE-first
OpenAI-flagship coding agent ships:

* ``chimera ferret apply [--last]`` — apply the latest agent diff via
  ``git apply``.
* ``chimera ferret review <target>`` — non-interactive code review backed
  by :class:`chimera.review.orchestrator.ReviewOrchestrator`.
* ``chimera ferret fork <session-id> [--last] [--all]`` — fork an
  existing eventlog session into a new run id (in-place branch).
* ``chimera ferret mcp-server`` — run ferret as a JSON-RPC 2.0 MCP server
  on stdio so other agents can drive it as a tool host.
* ``chimera ferret mcp {add,list,remove}`` — manage the persisted
  ``~/.chimera/ferret/mcp_servers.json`` config of MCP server launchers.

Each handler lives in its own module so :mod:`chimera.ferret.cli` stays
short and the dispatch table can be kept in :data:`HANDLERS` for the
``cli`` to register late-bound. The handlers all take the parsed
``argparse.Namespace`` and return an integer process exit code.
"""

from __future__ import annotations

from typing import Any, Callable

# WHY: late-binding the handlers keeps the import cost of
# ``chimera.ferret.cli`` low — the cli only needs the dispatch table at
# ``run()`` time, not at parser-build time.

__all__ = [
    "HANDLERS",
    "MCP_ACTIONS",
    "dispatch_apply",
    "dispatch_review",
    "dispatch_fork",
    "dispatch_mcp_server",
    "dispatch_mcp",
]


def dispatch_apply(args: Any) -> int:
    """Forward to :func:`chimera.ferret.subcommands.apply.run_apply`."""
    from chimera.ferret.subcommands.apply import run_apply

    return int(run_apply(args))


def dispatch_review(args: Any) -> int:
    """Forward to :func:`chimera.ferret.subcommands.review.run_review`."""
    from chimera.ferret.subcommands.review import run_review

    return int(run_review(args))


def dispatch_fork(args: Any) -> int:
    """Forward to :func:`chimera.ferret.subcommands.fork.run_fork`."""
    from chimera.ferret.subcommands.fork import run_fork

    return int(run_fork(args))


def dispatch_mcp_server(args: Any) -> int:
    """Forward to :func:`chimera.ferret.subcommands.mcp_server.run_mcp_server`."""
    from chimera.ferret.subcommands.mcp_server import run_mcp_server

    return int(run_mcp_server(args))


def dispatch_mcp(args: Any) -> int:
    """Forward to :func:`chimera.ferret.subcommands.mcp_manage.run_mcp`."""
    from chimera.ferret.subcommands.mcp_manage import run_mcp

    return int(run_mcp(args))


# Map subcommand spelling → dispatcher. Mirrors the layout in
# :data:`chimera.ferret.cli._SUBCOMMAND_DISPATCH`. Kept here so the cli
# module can ``dict.update`` it onto its own table without duplicating
# the per-name string literals.
HANDLERS: dict[str, Callable[[Any], int]] = {
    "apply": dispatch_apply,
    "review": dispatch_review,
    "fork": dispatch_fork,
    "mcp-server": dispatch_mcp_server,
    "mcp": dispatch_mcp,
}

# MCP-manage sub-actions accepted by :func:`dispatch_mcp`. The cli adds
# these to ``_VALID_SUB_ACTIONS`` so argparse's ``choices`` validator
# accepts ``ferret mcp add``, ``ferret mcp list``, ``ferret mcp remove``.
MCP_ACTIONS: tuple[str, ...] = ("add", "list", "remove")

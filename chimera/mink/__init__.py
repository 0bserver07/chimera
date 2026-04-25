"""Mink — a Chimera coding agent.

Mink is a streaming, tool-using REPL built on Chimera's existing
:class:`AgentLoop`, :class:`LoopConfig`, tool registry, permissions, and
session primitives. Its default backend is **Kimi K2.6** served by Ollama
(``kimi-k2.6:cloud``), with a local-model fallback when the primary tag is
unreachable.

Public surface re-exported here:

* :func:`run` — entry point invoked by ``chimera mink``.
* :func:`add_arguments` — register the CLI flag matrix on a parser.
* :class:`MinkSettings` / :class:`MinkSettingsError` — settings dataclass + error.
* :func:`load_mink_settings` — load + merge ``settings.json`` layers.
* :class:`Permissions` — settings sub-block dataclass.
* :class:`Team` — experimental agent-teams primitive (gated by
  ``CHIMERA_EXPERIMENTAL_AGENT_TEAMS=1``).
"""
from __future__ import annotations

from chimera.mink.cli import add_arguments, run
from chimera.mink.settings import (
    MinkSettings,
    MinkSettingsError,
    Permissions,
    load_mink_settings,
)
from chimera.mink.team import Team

__all__ = [
    "MinkSettings",
    "MinkSettingsError",
    "Permissions",
    "Team",
    "add_arguments",
    "load_mink_settings",
    "run",
]

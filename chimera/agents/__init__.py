"""Agent configuration, registry, and preset agents."""
from __future__ import annotations

from chimera.agents.config import AgentConfig
from chimera.agents.loader import AgentFactory, AgentLoader, FileAgentDef
from chimera.agents.presets import (
    AgentPreset,
    BuildAgent,
    ExploreAgent,
    GeneralAgent,
    PlanAgent,
    ReviewAgent,
)
from chimera.agents.registry import AgentRegistry
from chimera.agents.team_roles import discover_team_roles

__all__ = [
    "AgentConfig",
    "AgentFactory",
    "AgentLoader",
    "AgentPreset",
    "AgentRegistry",
    "BuildAgent",
    "ExploreAgent",
    "FileAgentDef",
    "GeneralAgent",
    "PlanAgent",
    "ReviewAgent",
    "discover_team_roles",
]

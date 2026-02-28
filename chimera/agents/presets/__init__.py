"""Pre-built agent configurations for common workflows."""
from __future__ import annotations

from chimera.agents.presets.build import BuildAgent
from chimera.agents.presets.explore import ExploreAgent
from chimera.agents.presets.general import GeneralAgent
from chimera.agents.presets.plan import PlanAgent
from chimera.agents.presets.review import ReviewAgent

__all__ = [
    "BuildAgent",
    "ExploreAgent",
    "GeneralAgent",
    "PlanAgent",
    "ReviewAgent",
]

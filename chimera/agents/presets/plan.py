"""Plan agent preset: read-only analysis with plan-and-execute loop."""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from chimera.agents.config import AgentConfig

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.providers.base import Provider

__all__ = ["PLAN_CONFIG", "PlanAgent"]

PLAN_CONFIG = AgentConfig(
    name="plan",
    description="Read-only planning agent that creates execution plans.",
    system_prompt=(
        "You are a planning agent. Analyse the codebase, understand the "
        "architecture, and produce a detailed step-by-step plan for the "
        "requested change. You have read-only access to the repository."
    ),
    tools=["read_file", "search", "list_files", "repo_map"],
    permissions="read_only",
    loop="plan_execute",
    max_steps=50,
)


def PlanAgent(
    provider: Provider,
    env: Environment | None = None,
    **overrides: Any,
) -> Agent:
    """Create a plan agent with optional config overrides."""
    config = dataclasses.replace(PLAN_CONFIG, **overrides) if overrides else PLAN_CONFIG
    return config.build(provider, env)

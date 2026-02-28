"""Explore agent preset: search and read tools for codebase exploration."""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from chimera.agents.config import AgentConfig

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.providers.base import Provider

__all__ = ["EXPLORE_CONFIG", "ExploreAgent"]

EXPLORE_CONFIG = AgentConfig(
    name="explore",
    description="Codebase exploration agent with search and read tools.",
    system_prompt=(
        "You are an exploration agent. Your job is to navigate and understand "
        "a codebase. Search for files, read source code, and build a mental "
        "model of the project structure and architecture."
    ),
    tools=["read_file", "search", "list_files", "repo_map"],
    permissions="read_only",
    loop="react",
    max_steps=50,
)


def ExploreAgent(
    provider: Provider,
    env: Environment | None = None,
    **overrides: object,
) -> Agent:
    """Create an explore agent with optional config overrides."""
    config = dataclasses.replace(EXPLORE_CONFIG, **overrides) if overrides else EXPLORE_CONFIG
    return config.build(provider, env)

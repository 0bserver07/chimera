"""Review agent preset: read + git tools for code review."""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from chimera.agents.config import AgentConfig

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.providers.base import Provider

__all__ = ["REVIEW_CONFIG", "ReviewAgent"]

REVIEW_CONFIG = AgentConfig(
    name="review",
    description="Code review agent with read and git tools.",
    system_prompt=(
        "You are a code review agent. Read source files, inspect git diffs, "
        "and provide thorough, constructive code reviews. Focus on correctness, "
        "readability, test coverage, and potential issues."
    ),
    tools=["read_file", "search", "list_files", "git", "repo_map"],
    permissions="read_only",
    loop="react",
    max_steps=50,
)


def ReviewAgent(
    provider: Provider,
    env: Environment | None = None,
    **overrides: Any,
) -> Agent:
    """Create a review agent with optional config overrides."""
    config = dataclasses.replace(REVIEW_CONFIG, **overrides) if overrides else REVIEW_CONFIG
    return config.build(provider, env)

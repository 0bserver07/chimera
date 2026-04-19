"""General agent preset: all tools with auto-approve permissions."""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from chimera.agents.config import AgentConfig

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.providers.base import Provider

__all__ = ["GENERAL_CONFIG", "GeneralAgent"]

GENERAL_CONFIG = AgentConfig(
    name="general",
    description="General-purpose agent with all tools and auto-approve permissions.",
    system_prompt=(
        "You are a general-purpose coding agent with full access to all tools. "
        "You can read, write, and edit files, run commands, search the codebase, "
        "execute tests, and interact with git. Solve the task efficiently."
    ),
    tools=[
        "read_file", "write_file", "edit_file", "bash",
        "search", "list_files", "test", "git",
    ],
    permissions="auto_approve",
    loop="react",
    max_steps=50,
)


def GeneralAgent(
    provider: Provider,
    env: Environment | None = None,
    **overrides: Any,
) -> Agent:
    """Create a general agent with optional config overrides."""
    config = dataclasses.replace(GENERAL_CONFIG, **overrides) if overrides else GENERAL_CONFIG
    return config.build(provider, env)

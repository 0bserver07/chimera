"""Build agent preset: full code generation and modification capabilities."""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from chimera.agents.config import AgentConfig

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.providers.base import Provider

__all__ = ["BUILD_CONFIG", "BuildAgent"]

BUILD_CONFIG = AgentConfig(
    name="build",
    description="Code generation and modification agent with full tool access.",
    system_prompt=(
        "You are an expert coding agent. You can read, write, and edit files, "
        "run shell commands, search the codebase, and execute tests. "
        "Follow best practices: write clean, well-tested code. "
        "Always verify your changes by running the relevant tests."
    ),
    tools=[
        "read_file", "write_file", "edit_file", "bash",
        "search", "list_files", "test",
    ],
    permissions="interactive",
    loop="react",
    max_steps=100,
)


def BuildAgent(
    provider: Provider,
    env: Environment | None = None,
    **overrides: object,
) -> Agent:
    """Create a build agent with optional config overrides."""
    config = dataclasses.replace(BUILD_CONFIG, **overrides) if overrides else BUILD_CONFIG
    return config.build(provider, env)

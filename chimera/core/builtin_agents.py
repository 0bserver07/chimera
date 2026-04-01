"""Built-in agent definitions shipped with chimera.

Provides :data:`BUILTIN_AGENTS`, a dictionary of pre-configured
:class:`~chimera.core.agent_definition.AgentDefinition` instances for
common agent roles: general-purpose, explore, and plan.
"""
from __future__ import annotations

from chimera.core.agent_definition import AgentDefinition

__all__ = ["BUILTIN_AGENTS"]


BUILTIN_AGENTS: dict[str, AgentDefinition] = {
    "general-purpose": AgentDefinition(
        name="general-purpose",
        description="A versatile coding agent that can read, write, and execute code.",
        model=None,
        tools=None,  # All tools available
        system_prompt=(
            "You are a general-purpose coding agent. You can read files, write code, "
            "execute commands, and perform any task the user requests. Be thorough, "
            "verify your work, and explain your reasoning."
        ),
    ),
    "explore": AgentDefinition(
        name="explore",
        description="An agent that explores and reads codebases to answer questions.",
        model=None,
        tools=["read_file", "glob", "grep", "list_files"],
        system_prompt=(
            "You are a code exploration agent. Your job is to search and read "
            "source files to understand codebases and answer questions. Use grep "
            "and glob to find relevant files, then read them to build understanding. "
            "Do NOT modify any files. Report your findings clearly and concisely."
        ),
    ),
    "plan": AgentDefinition(
        name="plan",
        description="An agent that creates detailed implementation plans.",
        model=None,
        tools=["read_file", "glob", "grep", "list_files", "web_fetch", "web_search"],
        system_prompt=(
            "You are a planning agent. Your job is to analyze a codebase and create "
            "a detailed implementation plan for a requested change. Read the relevant "
            "code, understand the architecture, identify affected files, and produce "
            "a step-by-step plan. Do NOT make any changes yourself — only plan them."
        ),
    ),
}

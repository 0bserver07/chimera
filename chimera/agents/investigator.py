"""Structured subagent investigator.

A specialized read-only microagent that analyzes the codebase before the main
agent acts. Returns structured analysis: relevant files, dependencies, test
files, and suggested approach.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chimera.agents.microagent import MicroagentConfig, MicroagentSpawner

if TYPE_CHECKING:
    from chimera.env.base import Environment
    from chimera.providers.base import Provider
    from chimera.core.tool import BaseTool


@dataclass
class Investigation:
    """Structured result from a codebase investigation."""

    relevant_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    suggested_approach: str = ""
    raw_output: str = ""

    def to_context_block(self) -> str:
        """Format as a context block for the main agent."""
        parts = ["[Codebase Investigation]"]
        if self.relevant_files:
            parts.append(f"Relevant files: {', '.join(self.relevant_files)}")
        if self.test_files:
            parts.append(f"Test files: {', '.join(self.test_files)}")
        if self.dependencies:
            parts.append(f"Dependencies: {', '.join(self.dependencies)}")
        if self.suggested_approach:
            parts.append(f"Suggested approach: {self.suggested_approach}")
        return "\n".join(parts)


# Read-only tools for investigation
_INVESTIGATION_TOOLS = ["read_file", "search", "list_files"]

_INVESTIGATION_PROMPT = """You are a codebase investigator. Your job is to analyze the repository
and return a structured summary for another agent that will make changes.

Given the task, you must:
1. Find the most relevant source files
2. Find related test files
3. Identify key dependencies and imports
4. Suggest an approach

Return your analysis in this exact format:
RELEVANT_FILES: file1.py, file2.py
TEST_FILES: test_file1.py, test_file2.py
DEPENDENCIES: module1, module2
APPROACH: Brief description of what to do

Do NOT modify any files. Only read and search."""


def _parse_investigation(output: str) -> Investigation:
    """Parse structured output from the investigator agent."""
    inv = Investigation(raw_output=output)

    for line in output.split("\n"):
        line = line.strip()
        if line.startswith("RELEVANT_FILES:"):
            inv.relevant_files = [f.strip() for f in line[15:].split(",") if f.strip()]
        elif line.startswith("TEST_FILES:"):
            inv.test_files = [f.strip() for f in line[11:].split(",") if f.strip()]
        elif line.startswith("DEPENDENCIES:"):
            inv.dependencies = [d.strip() for d in line[13:].split(",") if d.strip()]
        elif line.startswith("APPROACH:"):
            inv.suggested_approach = line[9:].strip()

    return inv


class InvestigatorAgent:
    """Spawns a read-only investigator to analyze the codebase.

    Example::

        investigator = InvestigatorAgent(provider, tools)
        result = investigator.investigate("Fix the auth bug in login.py", env)
        print(result.relevant_files)
        print(result.to_context_block())
    """

    def __init__(
        self,
        provider: Provider,
        available_tools: list[BaseTool],
        max_steps: int = 10,
        max_cost: float = 0.0,
    ) -> None:
        self._spawner = MicroagentSpawner(provider, available_tools)
        self._max_steps = max_steps
        self._max_cost = max_cost

    def investigate(
        self,
        task: str,
        env: Environment | None = None,
    ) -> Investigation:
        """Run the investigator on a task.

        Args:
            task: The task the main agent will perform.
            env: Environment to investigate.

        Returns:
            Structured Investigation result.
        """
        config = MicroagentConfig(
            name="investigator",
            task=f"Investigate the codebase for this task: {task}\n\n{_INVESTIGATION_PROMPT}",
            tools=_INVESTIGATION_TOOLS,
            max_steps=self._max_steps,
            max_cost=self._max_cost,
            system_prompt=_INVESTIGATION_PROMPT,
        )

        result = self._spawner.spawn(config, env=env)
        return _parse_investigation(result.output)

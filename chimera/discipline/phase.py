"""Phased workflow execution with gate enforcement.

Phases execute in order.  Each phase has a completion gate -- a simple
callable that returns ``True`` when the phase's goal is satisfied.
If a gate fails, the phase retries up to *max_retries* before giving up.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment

from chimera.types import AgentResult

__all__ = ["Gate", "Phase", "PhasedWorkflow"]


@dataclass
class Gate:
    """Verifiable condition for phase advancement.

    Attributes:
        name: Unique identifier for the gate.
        check: Callable returning ``True`` if the gate passes.
        description: Human-readable explanation of what the gate checks.
    """

    name: str
    check: Callable[..., bool]
    description: str = ""


@dataclass
class Phase:
    """Workflow phase: goal + steps + completion gate.

    Attributes:
        number: Phase ordinal (1-based).
        name: Short label (e.g. ``"understand"``).
        goal: One-sentence description of what this phase achieves.
        steps: Ordered list of actions to perform.
        gate: Completion condition that must pass before advancing.
        read_only: If ``True``, the phase restricts write tools (advisory).
    """

    number: int
    name: str
    goal: str
    steps: list[str] = field(default_factory=list)
    gate: Gate = field(default_factory=lambda: Gate(name="default", check=lambda: True))
    read_only: bool = False


class PhasedWorkflow:
    """Execute ordered phases with gate enforcement.

    For each phase:
    1. Run agent with the phase goal as task prefix.
    2. Check gate -- pass? advance.
    3. Fail? Retry up to *max_retries* with failure context.
    4. Still failing? Return ``AgentResult(success=False, ...)``.
    """

    def __init__(self, phases: list[Phase], max_retries: int = 2) -> None:
        self._phases = phases
        self._max_retries = max_retries
        self._current_index = 0
        self._completed: list[Phase] = []

    def run(self, agent: Agent, task: str, env: Environment | None) -> AgentResult:
        """Execute phases sequentially.

        Args:
            agent: The agent instance to run each phase with.
            task: The overall task description.
            env: Execution environment (may be ``None``).

        Returns:
            Combined :class:`AgentResult` on success, or a failure result
            if a gate cannot be satisfied after retries.
        """
        total_steps = 0
        total_tool_calls = 0
        total_cost = 0.0
        combined_output_parts: list[str] = []

        for i, phase in enumerate(self._phases):
            self._current_index = i
            phase_task = f"[Phase {phase.number}: {phase.name}] {phase.goal}\n\nOverall task: {task}"

            result = agent.run(phase_task, env)
            total_steps += result.steps
            total_tool_calls += result.tool_calls_total
            total_cost += result.cost
            combined_output_parts.append(result.output)

            # Check gate.
            if phase.gate.check():
                self._completed.append(phase)
                continue

            # Gate failed -- retry.
            for _attempt in range(self._max_retries):
                retry_task = (
                    f"[Phase {phase.number}: {phase.name} — RETRY] "
                    f"Gate '{phase.gate.name}' failed. {phase.goal}\n\nOverall task: {task}"
                )
                result = agent.run(retry_task, env)
                total_steps += result.steps
                total_tool_calls += result.tool_calls_total
                total_cost += result.cost
                combined_output_parts.append(result.output)

                if phase.gate.check():
                    self._completed.append(phase)
                    break
            else:
                # Exhausted retries.
                return AgentResult(
                    output="\n".join(combined_output_parts),
                    steps=total_steps,
                    tool_calls_total=total_tool_calls,
                    cost=total_cost,
                    success=False,
                    error=f"Gate '{phase.gate.name}' failed after {self._max_retries} retries",
                )

        return AgentResult(
            output="\n".join(combined_output_parts),
            steps=total_steps,
            tool_calls_total=total_tool_calls,
            cost=total_cost,
            success=True,
        )

    @property
    def current_phase(self) -> Phase | None:
        """The phase currently being executed, or ``None`` if finished."""
        if self._current_index < len(self._phases):
            return self._phases[self._current_index]
        return None

    @property
    def completed_phases(self) -> list[Phase]:
        """Phases that have passed their gates."""
        return list(self._completed)

"""AgentRunner — the one contract that makes any agent drop into the matrix.

The eval :class:`~chimera.eval.harness.Harness` drives
``agent.run(prompt, env) -> AgentResult``. That works for in-process agents but
says nothing about *external* agents (an ACP subprocess, a CLI, a third-party
SWE-bench harness). :class:`AgentRunner` is the wider contract every runner
satisfies, and :class:`AgentRunResult` is the normalized output the
agent × benchmark matrix aggregates — one row per agent, one cell per
(agent, benchmark). See ``docs/specs/agent-benchmark-matrix.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from chimera.env.base import Environment


@dataclass
class AgentRunResult:
    """Normalized output of one agent attempt at one task.

    Attributes:
        patch: Unified diff, for SWE-style repo-fix tasks. ``None`` when the
            agent produced free text rather than a diff.
        answer: Free-text answer, for code-generation / QA tasks.
        trajectory: ATIF v1.7 trajectory dict when emitted, else ``None``
            (populated by the trajectory-emission phase).
        cost_usd: Total dollar cost of the attempt.
        tool_calls: Tool-call count — the normalized budget unit for agents
            that route through ``chimera/core/tool_executor.py``.
        llm_calls: API-turn count.
        wall_clock_sec: Wall-clock duration of the attempt.
        status: ``completed`` | ``budget_exhausted`` | ``error`` | ``timeout``.
        raw: Runner-specific extras (native result, stderr, exit code, …).
    """

    patch: str | None = None
    answer: str = ""
    trajectory: dict[str, Any] | None = None
    cost_usd: float = 0.0
    tool_calls: int = 0
    llm_calls: int = 0
    wall_clock_sec: float = 0.0
    status: str = "completed"
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentRunner(Protocol):
    """Anything that can attempt a benchmark task and return a normalized result.

    Implementations:
    :class:`~chimera.eval.runners.in_process.InProcessRunner` (Chimera agents)
    today; ACP, CLI-template, and native-harness runners for external agents
    follow (see the spec). The ``id`` labels the agent's row in the matrix.
    """

    id: str

    def run(
        self,
        task: Any,
        env: Environment | None = None,
        budget: Any = None,
    ) -> AgentRunResult:
        """Attempt *task* in *env* under an optional *budget*.

        Args:
            task: A benchmark task (dict with a ``prompt``/``problem`` key, or
                an object exposing one, or a raw prompt string).
            env: Optional execution environment for the attempt.
            budget: Optional budget spec; enforcement is runner-dependent and
                degrades gracefully when a runner cannot honor every dimension.

        Returns:
            An :class:`AgentRunResult`.
        """
        ...

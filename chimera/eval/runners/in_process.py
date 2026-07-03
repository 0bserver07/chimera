"""InProcessRunner — run a Chimera agent as an :class:`AgentRunner`.

Wraps either a ready agent or an ``agent_factory(provider) -> agent`` (the
contract :class:`~chimera.eval.comparative.ComparativeEval` already uses) behind
the :class:`~chimera.eval.runners.base.AgentRunner` protocol, mapping the native
:class:`~chimera.types.AgentResult` onto
:class:`~chimera.eval.runners.base.AgentRunResult`. This is the in-process arm of
the agent × benchmark matrix; see ``docs/specs/agent-benchmark-matrix.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from chimera.eval.runners.base import AgentRunResult

if TYPE_CHECKING:
    from chimera.env.base import Environment

_PROMPT_KEYS = ("prompt", "problem", "question", "instruction", "task")


def _prompt_of(task: Any) -> str:
    """Best-effort extraction of the input prompt from a benchmark task."""
    if isinstance(task, str):
        return task
    if isinstance(task, dict):
        for key in _PROMPT_KEYS:
            val = task.get(key)
            if isinstance(val, str) and val:
                return val
        return str(task)
    for attr in _PROMPT_KEYS:
        val = getattr(task, attr, None)
        if isinstance(val, str) and val:
            return val
    return str(task)


def _to_run_result(result: Any) -> AgentRunResult:
    """Map a native :class:`~chimera.types.AgentResult` onto AgentRunResult."""
    success = bool(getattr(result, "success", True))
    error = getattr(result, "error", None)
    return AgentRunResult(
        answer=str(getattr(result, "output", "") or ""),
        cost_usd=float(getattr(result, "cost", 0.0) or 0.0),
        tool_calls=int(getattr(result, "tool_calls_total", 0) or 0),
        llm_calls=int(getattr(result, "steps", 0) or 0),
        status="completed" if (success and not error) else "error",
        raw={"success": success, "error": error, "steps": getattr(result, "steps", None)},
    )


class InProcessRunner:
    """Adapt a Chimera agent (or a factory) to the AgentRunner protocol.

    Args:
        id: Row label for the matrix.
        agent: A ready agent exposing ``run(prompt, env) -> AgentResult``.
            Mutually exclusive with *agent_factory*.
        agent_factory: ``factory(provider) -> agent``, constructed lazily on the
            first :meth:`run`. Mirrors ``ComparativeEval.add_config`` so existing
            factories drop in unchanged.
        provider: Provider passed to *agent_factory* when it is used.

    Raises:
        ValueError: If neither *agent* nor *agent_factory* is supplied.
    """

    def __init__(
        self,
        id: str,
        agent: Any = None,
        agent_factory: Callable[[Any], Any] | None = None,
        provider: Any = None,
    ) -> None:
        if agent is None and agent_factory is None:
            raise ValueError("InProcessRunner needs either 'agent' or 'agent_factory'")
        self.id = id
        self._agent = agent
        self._factory = agent_factory
        self._provider = provider

    def _resolve(self) -> Any:
        """Return the agent, constructing it from the factory on first use."""
        if self._agent is None:
            assert self._factory is not None  # guaranteed by __init__
            self._agent = self._factory(self._provider)
        return self._agent

    def run(
        self,
        task: Any,
        env: Environment | None = None,
        budget: Any = None,  # noqa: ARG002 - honored by later budget-parity phase
    ) -> AgentRunResult:
        """Run the agent against *task* and normalize its result."""
        agent = self._resolve()
        native = agent.run(_prompt_of(task), env)
        return _to_run_result(native)

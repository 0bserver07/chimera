"""InProcessRunner — run a Chimera agent as an :class:`AgentRunner`.

Wraps either a ready agent or an ``agent_factory(provider) -> agent`` (the
contract :class:`~chimera.eval.comparative.ComparativeEval` already uses) behind
the :class:`~chimera.eval.runners.base.AgentRunner` protocol, mapping the native
:class:`~chimera.types.AgentResult` onto
:class:`~chimera.eval.runners.base.AgentRunResult`. This is the in-process arm of
the agent × benchmark matrix; see ``docs/specs/agent-benchmark-matrix.md``.

**Budget enforcement.** When :meth:`InProcessRunner.run` is handed a
:class:`~chimera.core.budget.BudgetSpec`, it rebuilds the agent with a per-task
:class:`~chimera.core.loop_config.LoopConfig` carrying a
:class:`~chimera.core.budget.BudgetEnforcer` and a shared
:class:`~chimera.core.cancellation.CancellationToken`, wraps the provider in a
:class:`~chimera.core.budget.BudgetedProvider`, and runs it — mirroring
``ComparativeEval.run_with_budget``. Factories that accept a second
``loop_config`` argument (the four loop-posture agents) get full tool-call
enforcement: the shared ``tool_executor`` records each completed call and the
enforcer trips the token at the cap, so the loop stops exactly the way it stops
for a user cancel. Factories that cannot accept a config (the assembly-preset
and loop-style agents, whose loops expose no config seam) still run under the
budgeted provider, but the cell is honestly flagged ``budget_honored=False`` —
no fake enforcement. A budget-stopped run is reported with
``status="budget_exhausted"`` so :func:`chimera.eval.matrix.run_matrix` can keep
budget hits distinct from task failures.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Callable

from chimera.eval.runners.base import AgentRunResult

if TYPE_CHECKING:
    from chimera.core.budget import BudgetEnforcer, BudgetSpec
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
        agent_factory: ``factory(provider) -> agent`` (or
            ``factory(provider, loop_config) -> agent`` for budget-aware
            factories), constructed lazily on the first :meth:`run`. Mirrors
            ``ComparativeEval.add_config`` so existing factories drop in
            unchanged.
        provider: Provider passed to *agent_factory* when it is used.

    Raises:
        ValueError: If neither *agent* nor *agent_factory* is supplied.
    """

    def __init__(
        self,
        id: str,
        agent: Any = None,
        agent_factory: Callable[..., Any] | None = None,
        provider: Any = None,
    ) -> None:
        if agent is None and agent_factory is None:
            raise ValueError("InProcessRunner needs either 'agent' or 'agent_factory'")
        self.id = id
        self._agent = agent
        self._factory = agent_factory
        self._provider = provider

    def _resolve(self) -> Any:
        """Return the agent, constructing it from the factory on first use.

        Only the **unbudgeted** path caches: a budgeted run always builds a
        fresh agent (see :meth:`_build_budgeted`) so per-task budget state
        never leaks into a later unbudgeted call, and vice versa.
        """
        if self._agent is None:
            assert self._factory is not None  # guaranteed by __init__
            self._agent = self._factory(self._provider)
        return self._agent

    def _factory_accepts_config(self) -> bool:
        """Whether the factory takes a second ``loop_config`` argument.

        Full tool-call budget enforcement requires injecting a
        :class:`~chimera.core.loop_config.LoopConfig` into the agent's loop,
        which is only possible when the factory accepts it. A single-argument
        factory (assembly presets / loop styles) cannot, so the run is flagged
        partial rather than given fake enforcement.
        """
        if self._factory is None:
            return False
        try:
            return len(inspect.signature(self._factory).parameters) >= 2
        except (TypeError, ValueError):
            return False

    def _build_budgeted(self, provider: Any, loop_config: Any) -> tuple[Any, bool]:
        """Build a fresh agent for a budgeted run.

        Returns ``(agent, honored)`` where *honored* is ``True`` only when the
        agent's loop actually received *loop_config* (and thus enforces the
        tool-call budget at the shared ``tool_executor`` choke point).

        *provider* is the :class:`~chimera.core.budget.BudgetedProvider`
        wrapping the run's enforcer, so LLM-call / cost / wall-clock caps are
        recorded even on the partial (single-arg) path.
        """
        if self._factory is None:
            # A ready agent cannot be rebuilt with a budget seam.
            return self._agent, False
        if self._factory_accepts_config():
            return self._factory(provider, loop_config), True
        return self._factory(provider), False

    def run(
        self,
        task: Any,
        env: Environment | None = None,
        budget: Any = None,
    ) -> AgentRunResult:
        """Run the agent against *task* and normalize its result.

        Args:
            task: A benchmark task (dict/obj with a prompt key, or a raw string).
            env: Optional per-task environment.
            budget: Optional :class:`~chimera.core.budget.BudgetSpec`. ``None``
                keeps the legacy unbudgeted (lazily-cached) behavior. When set,
                the agent is rebuilt per run under a per-task enforcer and the
                result carries ``status="budget_exhausted"`` plus
                ``raw["budget_honored"]`` / ``raw["budget_note"]`` flags.
        """
        if budget is None:
            agent = self._resolve()
            native = agent.run(_prompt_of(task), env)
            return _to_run_result(native)
        return self._run_budgeted(task, env, budget)

    def _run_budgeted(
        self,
        task: Any,
        env: Environment | None,
        budget: BudgetSpec,
    ) -> AgentRunResult:
        """Run under a per-task budget, mapping a budget stop to its own status."""
        from chimera.core.budget import BudgetEnforcer, BudgetedProvider
        from chimera.core.cancellation import CancellationToken, OperationCancelled
        from chimera.core.loop_config import LoopConfig
        from chimera.permissions.presets import AutoApprove

        token = CancellationToken()
        enforcer = BudgetEnforcer(budget, cancellation=token)
        loop_config = LoopConfig(
            budget_enforcer=enforcer,
            cancellation=token,
            permissions=AutoApprove(),
        )
        budgeted_provider = BudgetedProvider(self._provider, enforcer)
        agent, honored = self._build_budgeted(budgeted_provider, loop_config)
        note = "" if honored else "factory does not accept loop_config"

        enforcer.start()
        native: Any = None
        run_error: Exception | None = None
        try:
            native = agent.run(_prompt_of(task), env)
        except OperationCancelled:
            # The budget tripped mid-flight and the loop surfaced the
            # cooperative cancel instead of returning gracefully.
            native = None
        except Exception as exc:  # noqa: BLE001 — one task must not abort the grid
            run_error = exc

        return self._budgeted_result(native, enforcer, honored, note, run_error)

    @staticmethod
    def _budgeted_result(
        native: Any,
        enforcer: BudgetEnforcer,
        honored: bool,
        note: str,
        run_error: Exception | None,
    ) -> AgentRunResult:
        """Reduce a budgeted attempt to an :class:`AgentRunResult`.

        A budget hit (``enforcer.exhausted``) wins over an ordinary completion
        and maps to ``status="budget_exhausted"``; the enforcer's tally is the
        authoritative counter source since it survives a cancelled loop.
        """
        tally = enforcer.tally
        flags = {"budget_honored": honored, "budget_note": note}

        if run_error is not None:
            return AgentRunResult(
                answer="",
                cost_usd=tally.cost_usd,
                tool_calls=tally.tool_calls,
                llm_calls=tally.llm_calls,
                status="error",
                raw={**flags, "error": f"{type(run_error).__name__}: {run_error}"},
            )

        if enforcer.exhausted:
            answer = str(getattr(native, "output", "") or "") if native is not None else ""
            return AgentRunResult(
                answer=answer,
                cost_usd=tally.cost_usd or float(getattr(native, "cost", 0.0) or 0.0),
                tool_calls=tally.tool_calls,
                llm_calls=tally.llm_calls or int(getattr(native, "steps", 0) or 0),
                status="budget_exhausted",
                raw={**flags, "budget_reason": enforcer.exhausted_reason},
            )

        # Completed under the cap (or the factory could not be budgeted at all).
        result = _to_run_result(native)
        result.raw.update(flags)
        return result

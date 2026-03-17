"""Hyperparameter search for synthesis configurations.

Provides a :class:`SynthesisTuner` that performs grid search over a
:class:`SearchSpace` of hyperparameters, running synthesis for each
configuration and selecting the best one by the chosen metric.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

from chimera.training.strategies.base import Callback, SynthesisResult


@dataclass
class TrialResult:
    """Result of a single tuner trial.

    Attributes:
        config: The hyperparameter configuration used for this trial.
        synthesis_result: The full synthesis result from the trial.
        score: The primary metric value extracted from the result.
    """

    config: dict[str, Any]
    synthesis_result: SynthesisResult
    score: float


@dataclass
class TunerResult:
    """Aggregated result of a hyperparameter search.

    Attributes:
        best_config: The configuration that achieved the highest score.
        best_score: The score of the best configuration.
        trials: All trial results in execution order.
        total_cost: Sum of costs across all trials.
    """

    best_config: dict[str, Any]
    best_score: float
    trials: list[TrialResult]
    total_cost: float


class SearchSpace:
    """Define the hyperparameter search space.

    Supports categorical choices. Calling :meth:`configurations` produces
    the full Cartesian product (grid search) of all registered parameters.

    Example::

        space = SearchSpace()
        space.choice("model", ["claude-sonnet-4-20250514", "glm-5"])
        space.choice("max_steps", [10, 25])
        # Produces 4 configurations
    """

    def __init__(self) -> None:
        self._params: dict[str, list[Any]] = {}

    def choice(self, name: str, values: list[Any]) -> SearchSpace:
        """Add a categorical parameter.

        Args:
            name: Parameter name (used as key in config dicts).
            values: List of possible values for this parameter.

        Returns:
            Self, for fluent chaining.
        """
        self._params[name] = list(values)
        return self

    def configurations(self) -> list[dict[str, Any]]:
        """Generate all combinations (grid search).

        Returns:
            A list of config dicts, one per combination. Returns ``[{}]``
            if no parameters have been registered.
        """
        if not self._params:
            return [{}]
        keys = list(self._params.keys())
        values = list(self._params.values())
        return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


class SynthesisTuner:
    """Grid search over synthesis configurations.

    Creates a fresh environment for each trial, runs synthesis,
    and selects the best configuration by the chosen metric.

    Args:
        spec: The synthesis specification.
        env_factory: Callable that returns a fresh Environment for each trial.
        agent_factory: Optional callable that receives a config dict and
            returns an Agent. If ``None``, a default factory is used that
            reads ``model`` and ``max_steps`` from the config.
    """

    def __init__(
        self,
        spec: Any,  # Spec - use Any to avoid circular imports
        env_factory: Callable[[], Any],  # () -> Environment
        agent_factory: Callable[[dict[str, Any]], Any] | None = None,  # (config) -> Agent
    ) -> None:
        self._spec = spec
        self._env_factory = env_factory
        self._agent_factory = agent_factory

    def search(
        self,
        space: SearchSpace,
        max_trials: int | None = None,
        metric: str = "pass_rate",
        callbacks: list[Callback] | None = None,
    ) -> TunerResult:
        """Run synthesis for each configuration, return best.

        Args:
            space: The hyperparameter search space.
            max_trials: Maximum number of configurations to try. If ``None``,
                all configurations are tried.
            metric: Metric to optimise. Supported: ``"pass_rate"`` (default),
                ``"cost"`` (lower is better), ``"iterations"`` (fewer is better).
            callbacks: Optional callbacks forwarded to each synthesis run.

        Returns:
            A :class:`TunerResult` with the best configuration and all trials.
        """
        configs = space.configurations()
        if max_trials is not None:
            configs = configs[:max_trials]

        trials: list[TrialResult] = []
        total_cost = 0.0

        for config in configs:
            env = self._env_factory()
            try:
                env.setup()
                agent = self._build_agent(config)

                from chimera.training.trainer import Trainer

                strategy = self._build_strategy(
                    config.get("strategy", "convergence"), config,
                )

                trainer = Trainer(
                    spec=self._spec,
                    agent=agent,
                    env=env,
                )
                result = trainer.synthesize(
                    strategy=strategy, callbacks=callbacks or [],
                )

                score = self._extract_metric(result, metric)
                trials.append(
                    TrialResult(
                        config=config,
                        synthesis_result=result,
                        score=score,
                    )
                )
                total_cost += result.total_cost
            except Exception as exc:
                # Failed trial -- record with score 0
                failed = SynthesisResult(
                    converged=False,
                    iterations=0,
                    total_cost=0,
                    best_pass_rate=0,
                    history=[],
                    failure_reason=str(exc),
                )
                trials.append(
                    TrialResult(config=config, synthesis_result=failed, score=0.0)
                )
            finally:
                try:
                    env.cleanup()
                except Exception:
                    pass

        # Pick best
        if trials:
            best = max(trials, key=lambda t: t.score)
        else:
            best = TrialResult(
                config={},
                synthesis_result=SynthesisResult(
                    converged=False,
                    iterations=0,
                    total_cost=0,
                    best_pass_rate=0,
                    history=[],
                ),
                score=0.0,
            )

        return TunerResult(
            best_config=best.config,
            best_score=best.score,
            trials=trials,
            total_cost=total_cost,
        )

    def _build_agent(self, config: dict[str, Any]) -> Any:
        """Build an agent from config, using the custom or default factory."""
        if self._agent_factory:
            return self._agent_factory(config)
        # Default: use create_provider with model from config
        from chimera.providers.factory import create_provider
        from chimera.core.agent import Agent
        from chimera.core.loop import ReAct
        from chimera.core.tool_group import AGENT_TOOLS

        model = config.get("model")
        provider = create_provider(model=model)
        max_steps = config.get("max_steps", 25)
        return Agent(
            provider=provider,
            tools=list(AGENT_TOOLS),
            loop=ReAct(max_steps=max_steps),
        )

    def _build_strategy(self, name: str, config: dict[str, Any]) -> Any:
        """Build a strategy by name, using config for parameters."""
        from chimera.training.strategies.convergence import TestConvergence
        from chimera.training.strategies.passthrough import Passthrough

        if name == "convergence":
            return TestConvergence(
                max_iterations=config.get("max_iterations", 10),
            )
        elif name == "passthrough":
            return Passthrough()
        else:
            return TestConvergence(
                max_iterations=config.get("max_iterations", 10),
            )

    def _extract_metric(self, result: SynthesisResult, metric: str) -> float:
        """Extract a numeric score from a synthesis result.

        Args:
            result: The synthesis result.
            metric: One of ``"pass_rate"``, ``"cost"``, ``"iterations"``.

        Returns:
            A float score where higher is better.
        """
        if metric == "pass_rate":
            return result.best_pass_rate
        elif metric == "cost":
            return -result.total_cost  # lower cost = higher score
        elif metric == "iterations":
            return -result.iterations  # fewer iterations = higher score
        return result.best_pass_rate

# chimera/synthesize.py
"""Top-level synthesize() convenience function."""
from __future__ import annotations

from chimera.core.agent import Agent
from chimera.core.loop import ReAct
from chimera.core.tool_group import DEFAULT_TOOLS
from chimera.env.local import LocalEnvironment
from chimera.providers.factory import create_provider
from chimera.training.callbacks import CostLimit
from chimera.training.spec import Spec
from chimera.training.strategies.base import Callback, SynthesisResult
from chimera.training.strategies.convergence import TestConvergence
from chimera.training.trainer import Trainer


def synthesize(
    spec: str,
    *,
    tests: str | None = None,
    model: str = "claude-sonnet-4-20250514",
    workdir: str = ".",
    max_iterations: int = 50,
    patience: int = 5,
    max_cost: float | None = None,
    max_steps: int = 50,
) -> SynthesisResult:
    """Synthesize a codebase from a specification. One function, batteries included.

    Args:
        spec: What to build (text description or path to spec file).
        tests: Path to test directory. If provided, convergence = all tests pass.
        model: Model identifier (e.g. "claude-sonnet-4-20250514", "gpt-4o").
        workdir: Working directory for generated code.
        max_iterations: Maximum synthesis epochs.
        patience: Stop after this many epochs without improvement.
        max_cost: Optional dollar budget. Stops synthesis when exceeded.
        max_steps: Maximum agent steps per epoch.

    Returns:
        SynthesisResult with convergence status, cost, and history.
    """
    provider = create_provider(model=model)

    test_cmd = f"python -m pytest {tests} -v" if tests else "python -m pytest -v"
    env = LocalEnvironment(workdir=workdir, test_cmd=test_cmd)
    env.setup()

    agent = Agent(
        provider=provider,
        tools=list(DEFAULT_TOOLS),
        loop=ReAct(max_steps=max_steps),
    )

    if tests:
        spec_obj = Spec.from_tests(tests, spec)
    else:
        spec_obj = Spec.from_string(spec)

    callbacks: list[Callback] = []
    if max_cost is not None:
        callbacks.append(CostLimit(max_cost=max_cost))

    trainer = Trainer(spec=spec_obj, agent=agent, env=env)
    return trainer.synthesize(
        strategy=TestConvergence(max_iterations=max_iterations, patience=patience),
        callbacks=callbacks,
    )

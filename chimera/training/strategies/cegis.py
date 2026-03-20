"""Counterexample-Guided Inductive Synthesis (CEGIS) strategy.

Each epoch focuses on a single failing test (the counterexample) rather than
showing all failures at once.  This reduces oscillation where fixing one test
breaks another.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from chimera.training.strategies.base import (
    Callback,
    EpochResult,
    Strategy,
    SynthesisResult,
)

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment
    from chimera.training.constraint import Constraint
    from chimera.training.spec import Spec


class CEGISStrategy(Strategy):
    """Counterexample-Guided Inductive Synthesis.

    Each epoch:
    1. Run all tests
    2. If all pass -> converged
    3. Pick the FIRST failing test as the counterexample
    4. Prompt the agent with ONLY that failure
    5. Agent fixes it
    6. Repeat

    This focuses the agent on one problem at a time, reducing
    oscillation where fixing one test breaks another.

    Args:
        max_iterations: Maximum number of synthesis epochs.
        patience: Stop after this many consecutive epochs without
            a new test passing.
    """

    def __init__(
        self,
        max_iterations: int = 50,
        patience: int = 10,
    ) -> None:
        self._max_iterations = max_iterations
        self._patience = patience

    def run(
        self,
        agent: Agent,
        spec: Spec,
        env: Environment,
        constraints: list[Constraint] | None = None,
        callbacks: list[Callback] | None = None,
    ) -> SynthesisResult:
        """Execute the CEGIS strategy and return synthesis results.

        Args:
            agent: The agent that generates/modifies code.
            spec: The synthesis specification.
            env: The execution environment.
            constraints: Optional constraints that must be satisfied.
            callbacks: Optional observers for synthesis events.

        Returns:
            A SynthesisResult indicating whether synthesis converged.
        """
        callbacks = callbacks or []
        constraints = constraints or []
        for cb in callbacks:
            cb.on_synthesis_start()

        history: list[EpochResult] = []
        fixed_counterexamples: list[str] = []
        best_pass_rate = 0.0
        best_passed = 0
        best_checkpoint: str | None = None
        stale_epochs = 0
        total_cost = 0.0

        for epoch_num in range(1, self._max_iterations + 1):
            for cb in callbacks:
                cb.on_epoch_start(epoch_num)

            # Run tests to find the current state
            test_result = env.run_tests()

            # Converged? (all tests pass)
            if test_result.all_passed:
                # Check constraints
                constraints_ok = True
                if constraints:
                    for constraint in constraints:
                        cr = constraint.evaluate(env)
                        if not cr.satisfied:
                            constraints_ok = False

                if constraints_ok:
                    result = SynthesisResult(
                        converged=True,
                        iterations=epoch_num - 1 if epoch_num > 1 else 0,
                        total_cost=total_cost,
                        best_pass_rate=1.0,
                        history=history,
                    )
                    for cb in callbacks:
                        cb.on_synthesis_end(result)
                    return result

            # Extract first failure
            failure = self._extract_first_failure(test_result.output)

            # Build prompt with single counterexample
            prompt = self._build_cegis_prompt(spec, failure, fixed_counterexamples)

            # Run agent to fix it
            agent_result = agent.run(prompt, env)
            total_cost += agent_result.cost

            # Re-run tests to measure result of the fix
            post_result = env.run_tests()

            improved = post_result.passed > best_passed

            epoch = EpochResult(
                epoch=epoch_num,
                pass_rate=post_result.pass_rate,
                passed=post_result.passed,
                total=post_result.total,
                agent_output=agent_result.output,
                improved=improved,
                cost=agent_result.cost,
            )
            history.append(epoch)

            # Notify callbacks
            should_continue = True
            for cb in callbacks:
                ret = cb.on_epoch_end(epoch_num, epoch)
                if ret is False:
                    should_continue = False

            if improved:
                best_pass_rate = post_result.pass_rate
                best_passed = post_result.passed
                best_checkpoint = env.checkpoint()
                stale_epochs = 0
                # Record the fixed counterexample
                if failure:
                    fixed_counterexamples.append(failure)
            else:
                stale_epochs += 1
                # Rollback on regression
                if best_checkpoint is not None and post_result.pass_rate < best_pass_rate:
                    env.restore(best_checkpoint)

            # Converged after fix?
            if post_result.all_passed:
                constraints_ok = True
                if constraints:
                    for constraint in constraints:
                        cr = constraint.evaluate(env)
                        if not cr.satisfied:
                            constraints_ok = False

                if constraints_ok:
                    result = SynthesisResult(
                        converged=True,
                        iterations=epoch_num,
                        total_cost=total_cost,
                        best_pass_rate=best_pass_rate,
                        history=history,
                    )
                    for cb in callbacks:
                        cb.on_synthesis_end(result)
                    return result

            # Callback requested stop?
            if not should_continue:
                result = SynthesisResult(
                    converged=False,
                    iterations=epoch_num,
                    total_cost=total_cost,
                    best_pass_rate=best_pass_rate,
                    history=history,
                    failure_reason="Stopped by callback",
                )
                for cb in callbacks:
                    cb.on_synthesis_end(result)
                return result

            # Patience exhausted?
            if stale_epochs >= self._patience:
                result = SynthesisResult(
                    converged=False,
                    iterations=epoch_num,
                    total_cost=total_cost,
                    best_pass_rate=best_pass_rate,
                    history=history,
                    failure_reason=f"No improvement for {self._patience} epochs",
                )
                for cb in callbacks:
                    cb.on_synthesis_end(result)
                return result

        result = SynthesisResult(
            converged=False,
            iterations=self._max_iterations,
            total_cost=total_cost,
            best_pass_rate=best_pass_rate,
            history=history,
            failure_reason="Max iterations reached",
        )
        for cb in callbacks:
            cb.on_synthesis_end(result)
        return result

    def _extract_first_failure(self, test_output: str) -> str | None:
        """Parse test output for the first FAILED test name and error.

        Looks for pytest-style ``FAILED test_file::test_name`` lines and
        extracts the first one along with any associated error text.

        Args:
            test_output: Raw output from the test runner.

        Returns:
            A string describing the first failure, or None if no failures
            are found in the output.
        """
        lines = test_output.splitlines()

        # Look for "FAILED" lines (pytest style)
        for i, line in enumerate(lines):
            match = re.search(r"FAILED\s+(\S+)", line)
            if match:
                _ = match.group(1)  # failure_name
                # Collect context: lines around the failure
                context_lines = [line.strip()]
                # Look backwards for error/assertion info
                for j in range(max(0, i - 5), i):
                    stripped = lines[j].strip()
                    if stripped and stripped != line.strip():
                        context_lines.insert(-1, stripped)
                return "\n".join(context_lines)

        # Fallback: look for "FAIL:" or "ERROR:" lines
        for line in lines:
            match = re.search(r"(FAIL|ERROR):\s*(.+)", line)
            if match:
                return line.strip()

        # Last resort: return the whole output if it's short enough
        if test_output.strip():
            return test_output.strip()[:500]

        return None

    def _build_cegis_prompt(
        self, spec: Spec, failure: str | None, history: list[str]
    ) -> str:
        """Build a prompt focused on a single counterexample.

        The prompt includes the original spec, the one failing test that
        needs to be fixed, and a history of previously fixed counterexamples
        so the agent avoids regressing.

        Args:
            spec: The synthesis specification.
            failure: The single failing test description.
            history: List of previously fixed counterexample descriptions.

        Returns:
            A prompt string for the agent.
        """
        parts: list[str] = [spec.to_prompt()]

        if history:
            parts.append(
                "Previously fixed counterexamples (do NOT break these):\n"
                + "\n".join(f"  - {ce}" for ce in history)
            )

        if failure:
            parts.append(
                "Fix THIS failing test (focus only on this one):\n\n" + failure
            )
        else:
            parts.append("Some tests are still failing. Review and fix the code.")

        return "\n\n".join(parts)

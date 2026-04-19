"""Incremental synthesis strategy -- re-synthesize only failing functions.

Instead of re-prompting with the whole codebase, this strategy identifies
which functions are covered by failing tests and asks the agent to rewrite
only those functions.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
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


@dataclass
class SynthesisTarget:
    """A specific function that needs to be re-synthesized."""

    file: str
    function_name: str
    line_start: int
    line_end: int
    source: str  # current source of the function
    related_failure: str  # the test failure that led here


class IncrementalStrategy(Strategy):
    """Only re-synthesize functions affected by failing tests.

    Instead of re-prompting with the whole codebase, identifies which
    functions are covered by failing tests and asks the agent to rewrite
    only those functions.
    """

    def __init__(self, max_iterations: int = 20, patience: int = 5) -> None:
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
        """Execute the incremental strategy and return synthesis results.

        Args:
            agent: The agent to drive code generation.
            spec: Task specification describing what to synthesize.
            env: Execution environment with test suite.
            constraints: Optional constraints that must also be satisfied.
            callbacks: Optional observers for synthesis events.

        Returns:
            A SynthesisResult summarising convergence, cost, and history.
        """
        callbacks = callbacks or []
        for cb in callbacks:
            cb.on_synthesis_start()

        history: list[EpochResult] = []
        best_pass_rate = 0.0
        stale_count = 0
        total_cost = 0.0

        for epoch in range(1, self._max_iterations + 1):
            for cb in callbacks:
                cb.on_epoch_start(epoch)

            # Run tests
            test_result = env.run_tests()

            if test_result.all_passed:
                # Check constraints
                if constraints:
                    from chimera.training.constraint import all_satisfied, evaluate_all

                    results = evaluate_all(constraints, env)
                    if not all_satisfied(results):
                        # Constraints failed -- tell agent
                        msg = "; ".join(
                            r.message for r in results if not r.satisfied
                        )
                        result = agent.run(
                            f"Tests pass but constraints violated: {msg}. Fix the code.",
                            env=env,
                        )
                        epoch_result = EpochResult(
                            epoch=epoch,
                            pass_rate=test_result.pass_rate,
                            passed=test_result.passed,
                            total=test_result.total,
                            agent_output=result.output,
                            improved=True,
                            cost=result.cost,
                        )
                        total_cost += result.cost
                        history.append(epoch_result)
                        for cb in callbacks:
                            cb.on_epoch_end(epoch_result)
                        continue

                best_pass_rate = 1.0
                epoch_result = EpochResult(
                    epoch=epoch,
                    pass_rate=1.0,
                    passed=test_result.total,
                    total=test_result.total,
                    agent_output="All tests pass",
                    improved=True,
                    cost=0,
                )
                history.append(epoch_result)
                for cb in callbacks:
                    cb.on_epoch_end(epoch_result)
                break

            # Identify targets from test failures
            targets = self._identify_targets(env, test_result)

            if not targets:
                # Can't identify specific functions -- fall back to full prompt
                prompt = (
                    spec.to_prompt()
                    + f"\n\nTest failures:\n{test_result.output[:2000]}"
                )
                result = agent.run(prompt, env=env)
            else:
                # Targeted fix -- ask agent to fix specific function(s)
                target = targets[0]  # focus on first target
                prompt = self._build_targeted_prompt(spec, target, test_result)
                result = agent.run(prompt, env=env)

            total_cost += result.cost

            # Re-run tests to check progress
            new_result = env.run_tests()
            improved = new_result.pass_rate > best_pass_rate
            if improved:
                best_pass_rate = new_result.pass_rate
                stale_count = 0
            else:
                stale_count += 1

            epoch_result = EpochResult(
                epoch=epoch,
                pass_rate=new_result.pass_rate,
                passed=new_result.passed,
                total=new_result.total,
                agent_output=result.output,
                improved=improved,
                cost=result.cost,
            )
            history.append(epoch_result)

            for cb in callbacks:
                cb.on_epoch_end(epoch_result)

            if stale_count >= self._patience:
                break

        converged = best_pass_rate == 1.0
        sr = SynthesisResult(
            converged=converged,
            iterations=len(history),
            total_cost=total_cost,
            best_pass_rate=best_pass_rate,
            history=history,
            failure_reason=None if converged else "Did not converge",
        )
        for cb in callbacks:
            cb.on_synthesis_end(sr)
        return sr

    def _identify_targets(
        self, env: Environment, test_result: object
    ) -> list[SynthesisTarget]:
        """Extract file:line references from test failures and find enclosing functions.

        Args:
            env: The execution environment for reading source files.
            test_result: A TestResult with an ``output`` attribute containing
                the raw test runner output.

        Returns:
            A list of SynthesisTarget instances for each identified function.
        """
        targets: list[SynthesisTarget] = []
        # Parse file:line from test output (skip test files)
        pattern = re.compile(r"(\S+\.py):(\d+)")
        seen: set[str] = set()
        for match in pattern.finditer(test_result.output):  # type: ignore[attr-defined]
            filepath = match.group(1)
            line = int(match.group(2))
            if "test_" in filepath or filepath.startswith("test"):
                continue
            if filepath in seen:
                continue
            seen.add(filepath)

            # Find enclosing function using AST
            try:
                source = env.read_file(filepath)
                func = self._find_enclosing_function(source, line)
                if func:
                    targets.append(
                        SynthesisTarget(
                            file=filepath,
                            function_name=str(func["name"]),
                            line_start=int(func["start"]),  # type: ignore[arg-type]
                            line_end=int(func["end"]),  # type: ignore[arg-type]
                            source=str(func["source"]),
                            related_failure=test_result.output[:500],  # type: ignore[attr-defined]
                        )
                    )
            except Exception:
                pass
        return targets

    def _find_enclosing_function(
        self, source: str, line: int
    ) -> dict[str, object] | None:
        """Use AST to find the function containing the given line.

        Args:
            source: The full source code of the file.
            line: The 1-based line number to locate within a function.

        Returns:
            A dict with keys ``name``, ``start``, ``end``, ``source`` if a
            function is found, or ``None`` otherwise.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        lines = source.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.lineno <= line <= node.end_lineno:  # type: ignore[operator]
                    func_lines = lines[node.lineno - 1 : node.end_lineno]  # type: ignore[index]
                    return {
                        "name": node.name,
                        "start": node.lineno,
                        "end": node.end_lineno,
                        "source": "\n".join(func_lines),
                    }
        return None

    def _build_targeted_prompt(
        self, spec: Spec, target: SynthesisTarget, test_result: object
    ) -> str:
        """Build a prompt focused on fixing one specific function.

        Args:
            spec: The task specification.
            target: The identified function to fix.
            test_result: The test result containing failure output.

        Returns:
            A prompt string instructing the agent to fix only the target function.
        """
        return (
            f"{spec.to_prompt()}\n\n"
            f"## Targeted Fix\n\n"
            f"The bug is in `{target.function_name}()` in `{target.file}` "
            f"(lines {target.line_start}-{target.line_end}).\n\n"
            f"Current implementation:\n```python\n{target.source}\n```\n\n"
            f"Test failure:\n```\n{test_result.output[:1000]}\n```\n\n"  # type: ignore[attr-defined]
            f"Fix ONLY this function. Do not rewrite other code."
        )

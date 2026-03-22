"""Prompt engineering lab for systematic prompt optimization.

Swap system prompts while keeping the agent architecture constant, then
compare results across all variants.  Integrates with Chimera's modular
architecture so you can isolate prompt quality from tool/loop choices.

Example:
    ```python
    from chimera.eval.prompt_lab import PromptLab

    lab = PromptLab(provider, base_agent_factory, problems)
    lab.add_prompt("concise", "You are a concise coding assistant.")
    lab.add_prompt("verbose", "You are a thorough coding assistant. Think step by step.")
    report = lab.run()
    print(report.summary())
    print("Best prompt:", report.best_prompt())
    ```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from chimera.eval.comparative import TaskResult
from chimera.providers.base import Provider


@dataclass
class PromptReport:
    """Results comparing multiple prompt variants on the same problems.

    Attributes:
        results: Mapping from prompt name to list of per-problem results.
    """

    results: dict[str, list[TaskResult]]

    def best_prompt(self) -> str:
        """Return the name of the prompt variant with the highest pass rate.

        Ties are broken by lower total cost, then fewer total steps.

        Returns:
            Name of the best-performing prompt variant.

        Raises:
            ValueError: If no prompt variants have been evaluated.
        """
        if not self.results:
            raise ValueError("No prompt variants to compare")

        def _score(name: str) -> tuple[float, float, float]:
            task_results = self.results[name]
            total = len(task_results)
            passed = sum(1 for r in task_results if r.passed)
            pass_rate = passed / total if total > 0 else 0.0
            total_cost = sum(r.cost for r in task_results)
            total_steps = sum(r.steps for r in task_results)
            return (pass_rate, -total_cost, -total_steps)

        return max(self.results.keys(), key=_score)

    def summary(self) -> str:
        """Return a human-readable summary of prompt variant performance.

        Returns:
            Multi-line string with one row per prompt showing pass rate,
            average cost, and average steps.
        """
        lines: list[str] = []
        lines.append("Prompt Lab Summary")
        lines.append("=" * 40)
        for prompt_name, task_results in self.results.items():
            total = len(task_results)
            passed = sum(1 for r in task_results if r.passed)
            pass_rate = passed / total if total > 0 else 0.0
            avg_cost = (
                sum(r.cost for r in task_results) / total if total > 0 else 0.0
            )
            avg_steps = (
                sum(r.steps for r in task_results) / total if total > 0 else 0.0
            )
            lines.append(
                f"{prompt_name}: pass_rate={pass_rate:.1%}, "
                f"avg_cost=${avg_cost:.4f}, avg_steps={avg_steps:.1f}"
            )
        return "\n".join(lines)


class PromptLab:
    """Swap prompts while keeping agent architecture constant.

    The ``base_agent_factory`` receives ``(provider, system_prompt)`` and
    returns an agent-like object with a ``run(task, env)`` method.  Each
    prompt variant is tested on every problem in the set.

    Attributes:
        provider: The LLM provider used for all runs.
        base_agent_factory: Callable that builds an agent given a provider
            and a system prompt string.
        problems: List of problem dicts with ``"id"`` and ``"prompt"`` keys.
    """

    def __init__(
        self,
        provider: Provider,
        base_agent_factory: Callable[[Provider, str], Any],
        problems: list[dict[str, Any]],
    ) -> None:
        """Initialise a prompt lab.

        Args:
            provider: LLM provider shared across all prompt variants.
            base_agent_factory: Callable ``(provider, system_prompt) -> agent``
                that produces an agent for a given prompt.
            problems: List of problem dicts.  Each must have ``"id"`` and
                ``"prompt"`` keys; an ``"expected"`` key enables automatic
                pass/fail checking.
        """
        self.provider = provider
        self.base_agent_factory = base_agent_factory
        self.problems = problems
        self._prompts: dict[str, str] = {}

    def add_prompt(self, name: str, system_prompt: str) -> None:
        """Add a named prompt variant to test.

        Args:
            name: Human-readable identifier for this prompt variant.
            system_prompt: The system prompt string to inject.
        """
        self._prompts[name] = system_prompt

    def run(self) -> PromptReport:
        """Run all problems through all prompt variants.

        For each prompt, creates an agent via the base factory with that
        prompt, then runs every problem.  If a problem dict contains an
        ``"expected"`` key, the output is checked for substring containment;
        otherwise the task is marked as passed.

        Returns:
            A :class:`PromptReport` with per-prompt, per-problem results.
        """
        all_results: dict[str, list[TaskResult]] = {}

        for prompt_name, system_prompt in self._prompts.items():
            agent = self.base_agent_factory(self.provider, system_prompt)
            prompt_results: list[TaskResult] = []

            for problem in self.problems:
                prompt_text = problem.get("prompt", "")
                problem_id = problem.get("id", "unknown")

                agent_result = agent.run(prompt_text, None)

                # Determine pass/fail
                expected = problem.get("expected")
                if expected is not None:
                    passed = expected in agent_result.output
                else:
                    passed = True

                prompt_results.append(
                    TaskResult(
                        problem_id=problem_id,
                        output=agent_result.output,
                        cost=agent_result.cost,
                        steps=agent_result.steps,
                        passed=passed,
                    )
                )

            all_results[prompt_name] = prompt_results

        return PromptReport(results=all_results)

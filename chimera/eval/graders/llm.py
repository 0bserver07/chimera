"""LLM-based rubric grader: uses a provider to grade output against a rubric."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from chimera.eval.graders.base import GradeResult, Grader

if TYPE_CHECKING:
    from chimera.providers.base import Provider


class LLMRubricGrader(Grader):
    """Use an LLM to grade output against a rubric.

    Sends the task description, result output, and rubric to a provider,
    then parses a structured response with score and reasoning. Passes
    if the score is >= 0.7.

    Args:
        provider: The LLM provider to use for grading.
        rubric: The grading rubric text describing evaluation criteria.
    """

    name = "llm_rubric"

    def __init__(self, provider: Provider, rubric: str) -> None:
        self._provider = provider
        self._rubric = rubric

    def grade(self, task: dict[str, Any], result: dict[str, Any]) -> GradeResult:
        """Send task + result + rubric to provider and parse the grade.

        The provider is prompted to return JSON with "score" (0.0-1.0)
        and "reasoning" keys. Pass threshold is score >= 0.7.

        Args:
            task: The task dictionary (should contain 'prompt' or 'description').
            result: The result dictionary (should contain 'output').

        Returns:
            GradeResult based on the LLM's assessment.
        """
        task_desc = task.get("prompt", task.get("description", ""))
        output = result.get("output", "")

        prompt = (
            "You are grading an AI agent's output. Evaluate the output against "
            "the rubric and return a JSON object with exactly two keys:\n"
            '- "score": a float between 0.0 and 1.0\n'
            '- "reasoning": a brief explanation\n\n'
            f"Task: {task_desc}\n\n"
            f"Output: {output}\n\n"
            f"Rubric: {self._rubric}\n\n"
            "Return ONLY the JSON object, no other text."
        )

        messages = [{"role": "user", "content": prompt}]
        response = self._provider.complete(messages)  # type: ignore[arg-type]  # legacy call-site; providers tolerate dict messages

        try:
            parsed = json.loads(response.content)
            score = float(parsed.get("score", 0.0))
            reasoning = str(parsed.get("reasoning", ""))
        except (json.JSONDecodeError, TypeError, ValueError):
            return GradeResult(
                passed=False,
                score=0.0,
                reason=f"Failed to parse LLM response: {response.content}",
                grader_name=self.name,
            )

        # Clamp score to [0, 1]
        score = max(0.0, min(1.0, score))
        passed = score >= 0.7

        return GradeResult(
            passed=passed,
            score=score,
            reason=reasoning,
            grader_name=self.name,
        )

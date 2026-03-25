"""Grader base classes for evaluating agent task results."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class GradeResult:
    """Result of grading a single task.

    Args:
        passed: Whether the task passed the grading criteria.
        score: Numeric score between 0.0 and 1.0.
        reason: Human-readable explanation of the grade.
        grader_name: Name of the grader that produced this result.
    """

    passed: bool
    score: float
    reason: str = ""
    grader_name: str = ""


class Grader(ABC):
    """Grade an eval task result.

    Subclasses implement :meth:`grade` to evaluate whether an agent's
    output satisfies task requirements.
    """

    name: str = ""

    @abstractmethod
    def grade(self, task: dict[str, Any], result: dict[str, Any]) -> GradeResult:
        """Grade a task result.

        Args:
            task: The original task dictionary (contains prompt, id, etc.).
            result: The agent's result dictionary (contains output, etc.).

        Returns:
            A GradeResult with pass/fail, score, and reasoning.
        """
        ...

"""Critic base classes for in-loop action evaluation.

Provides :class:`Critic`, the abstract base for evaluating agent actions,
along with :class:`CriticResult`, :class:`CriticConfig`, and :class:`CriticMode`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.core.context import Context
    from chimera.types import Message, ToolCall


class CriticMode(str, Enum):
    """When the critic evaluates actions."""

    ALL_ACTIONS = "all_actions"
    FINISH_ONLY = "finish_only"
    TOOL_AND_FINISH = "tool_and_finish"


@dataclass
class CriticResult:
    """Result of a critic evaluation.

    Attributes:
        score: Evaluation score from 0.0 to 1.0.
        passed: Whether the score met the threshold.
        feedback: Actionable feedback for improvement.
        details: Optional structured metadata.
    """

    score: float
    passed: bool
    feedback: str | None = None
    details: dict[str, Any] | None = None


@dataclass
class CriticConfig:
    """Configuration for critic behavior.

    Attributes:
        mode: When to evaluate (all actions, finish only, or both).
        success_threshold: Minimum score to pass.
        max_refinement_iterations: Max retries before giving up.
        critic_model: Optional different model for the critic.
    """

    mode: CriticMode = CriticMode.FINISH_ONLY
    success_threshold: float = 0.8
    max_refinement_iterations: int = 3
    critic_model: str | None = None


class Critic(ABC):
    """Abstract base class for action evaluators.

    Args:
        config: Critic configuration. Defaults to :class:`CriticConfig`.
    """

    def __init__(self, config: CriticConfig | None = None) -> None:
        self.config = config or CriticConfig()

    @abstractmethod
    def evaluate(
        self, context: Context, current_action: Message | ToolCall,
    ) -> CriticResult:
        """Evaluate an action given the conversation context.

        Args:
            context: The current conversation context.
            current_action: The action to evaluate.

        Returns:
            A :class:`CriticResult` with score, pass/fail, and feedback.
        """

    def get_followup_prompt(self, result: CriticResult, iteration: int) -> str:
        """Generate a refinement prompt from critic feedback.

        Args:
            result: The critic evaluation result.
            iteration: Current refinement iteration number.

        Returns:
            A prompt string asking the agent to revise its response.
        """
        return (
            f"Your previous response scored {result.score:.1%} "
            f"(threshold: {self.config.success_threshold:.0%}). "
            f"Iteration {iteration}/{self.config.max_refinement_iterations}.\n\n"
            f"Feedback: {result.feedback}\n\n"
            f"Please revise your response addressing the feedback above."
        )

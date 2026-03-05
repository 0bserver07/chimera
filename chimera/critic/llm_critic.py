"""LLM-based and checklist-based critic implementations."""
from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.critic.base import Critic, CriticConfig, CriticResult

if TYPE_CHECKING:
    from chimera.core.context import Context
    from chimera.providers.base import Provider
    from chimera.types import Message, ToolCall


class LLMCritic(Critic):
    """Uses an LLM to evaluate agent actions.

    Args:
        provider: LLM provider for generating evaluations.
        config: Critic configuration.
        evaluation_prompt: Custom evaluation prompt template.
    """

    def __init__(
        self,
        provider: Provider,
        config: CriticConfig | None = None,
        evaluation_prompt: str | None = None,
    ) -> None:
        super().__init__(config)
        self.provider = provider
        self.evaluation_prompt = evaluation_prompt or self._default_prompt()

    def evaluate(
        self, context: Context, current_action: Message | ToolCall,
    ) -> CriticResult:
        """Evaluate an action using the LLM provider.

        Args:
            context: The current conversation context.
            current_action: The action to evaluate.

        Returns:
            A parsed :class:`CriticResult`.
        """
        messages = context.to_messages()
        prompt = self._build_eval_prompt(messages, current_action)
        response = self.provider.complete(
            [{"role": "user", "content": prompt}],
            model=self.config.critic_model,
        )
        return self._parse_result(response.content)

    def _default_prompt(self) -> str:
        return (
            "You are a code review critic. Evaluate the agent's latest action.\n\n"
            "Score from 0.0 to 1.0 based on:\n"
            "- Correctness: Does the action achieve the goal?\n"
            "- Safety: Are there any risky operations?\n"
            "- Efficiency: Is this the simplest approach?\n"
            "- Completeness: Does it handle edge cases?\n\n"
            "Respond in this exact format:\n"
            "SCORE: <float>\n"
            "PASSED: <true/false>\n"
            "FEEDBACK: <one paragraph of specific, actionable feedback>"
        )

    def _build_eval_prompt(
        self, messages: list[Message], current_action: Message | ToolCall,
    ) -> str:
        history = "\n".join(str(m) for m in messages[-5:])
        return (
            f"{self.evaluation_prompt}\n\n"
            f"Recent history:\n{history}\n\n"
            f"Current action:\n{current_action}"
        )

    def _parse_result(self, response: str) -> CriticResult:
        """Parse the LLM response into a CriticResult."""
        lines = response.strip().split("\n")
        score = 0.5
        passed = False
        feedback = response

        for line in lines:
            if line.startswith("SCORE:"):
                try:
                    score = float(line.split(":", 1)[1].strip())
                    score = max(0.0, min(1.0, score))
                except ValueError:
                    pass
            elif line.startswith("PASSED:"):
                passed = "true" in line.lower()
            elif line.startswith("FEEDBACK:"):
                feedback = line.split(":", 1)[1].strip()

        return CriticResult(score=score, passed=passed, feedback=feedback)


class ChecklistCritic(Critic):
    """Evaluates agent actions against a checklist of requirements.

    Args:
        checklist: List of requirement strings to evaluate against.
        provider: LLM provider for generating evaluations.
        config: Critic configuration.
    """

    def __init__(
        self,
        checklist: list[str],
        provider: Provider,
        config: CriticConfig | None = None,
    ) -> None:
        super().__init__(config)
        self.checklist = checklist
        self.provider = provider

    def evaluate(
        self, context: Context, current_action: Message | ToolCall,
    ) -> CriticResult:
        """Evaluate an action against the checklist.

        Args:
            context: The current conversation context.
            current_action: The action to evaluate.

        Returns:
            A :class:`CriticResult` based on checklist satisfaction.
        """
        prompt = self._build_prompt(context, current_action)
        response = self.provider.complete(
            [{"role": "user", "content": prompt}],
            model=self.config.critic_model,
        )
        return self._parse_result(response.content)

    def _build_prompt(
        self, context: Context, current_action: Message | ToolCall,
    ) -> str:
        items = "\n".join(f"- [ ] {item}" for item in self.checklist)
        return (
            "Evaluate whether the agent's work satisfies each requirement.\n"
            "Mark each item as [x] satisfied or [ ] not satisfied.\n\n"
            f"Requirements:\n{items}\n\n"
            f"Agent's latest action:\n{current_action}\n\n"
            "For each item, explain briefly. Then give an overall "
            "SCORE (fraction satisfied) and FEEDBACK."
        )

    def _parse_result(self, response: str) -> CriticResult:
        """Parse checklist evaluation response."""
        lines = response.strip().split("\n")
        score = 0.5
        feedback = response

        for line in lines:
            if line.startswith("SCORE:"):
                try:
                    score = float(line.split(":", 1)[1].strip())
                    score = max(0.0, min(1.0, score))
                except ValueError:
                    pass
            elif line.startswith("FEEDBACK:"):
                feedback = line.split(":", 1)[1].strip()

        passed = score >= self.config.success_threshold
        return CriticResult(score=score, passed=passed, feedback=feedback)

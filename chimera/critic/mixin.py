"""CriticMixin for integrating critic evaluation into reasoning loops."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.critic.base import CriticMode

if TYPE_CHECKING:
    from chimera.core.context import Context
    from chimera.critic.base import Critic
    from chimera.events.base import EventBus
    from chimera.types import Message, ToolCall


class CriticMixin:
    """Mixin for loops that support critic evaluation.

    Loops that inherit this mixin gain the ability to evaluate actions
    before committing to them and trigger iterative refinement when
    the critic score is below the configured threshold.

    Attributes:
        critic: The critic instance, or ``None`` to disable evaluation.
    """

    critic: Critic | None
    _refinement_iteration: int

    def _should_evaluate(self, action: Message | ToolCall) -> bool:
        """Check whether the critic should evaluate this action."""
        if self.critic is None:
            return False
        mode = self.critic.config.mode
        if mode == CriticMode.ALL_ACTIONS:
            return True
        if mode == CriticMode.FINISH_ONLY:
            return self._is_final_action(action)
        if mode == CriticMode.TOOL_AND_FINISH:
            return True
        return False

    def _evaluate_and_maybe_refine(
        self,
        context: Context,
        action: Message | ToolCall,
        event_bus: EventBus | None = None,
    ) -> tuple[bool, str | None]:
        """Evaluate an action and determine if refinement is needed.

        Args:
            context: The conversation context.
            action: The action to evaluate.
            event_bus: Optional event bus for emitting critic events.

        Returns:
            A tuple of (should_continue, followup_message). If should_continue
            is True, the loop should retry with the followup message.
        """
        if not self._should_evaluate(action):
            return False, None

        result = self.critic.evaluate(context, action)  # type: ignore[union-attr]

        if event_bus is not None:
            from chimera.events.types import CriticEvent

            event_bus.publish(CriticEvent(
                score=result.score,
                passed=result.passed,
                feedback=result.feedback,
                iteration=self._refinement_iteration,
            ))

        if result.passed:
            self._refinement_iteration = 0
            return False, None

        if self._refinement_iteration >= self.critic.config.max_refinement_iterations:  # type: ignore[union-attr]
            self._refinement_iteration = 0
            return False, None

        self._refinement_iteration += 1
        followup = self.critic.get_followup_prompt(  # type: ignore[union-attr]
            result, self._refinement_iteration,
        )
        return True, followup

    def _is_final_action(self, action: Any) -> bool:
        """Check if an action is the final action in a loop.

        Override in loop subclasses to define what counts as final.
        Default: action has no tool_calls (assistant text-only response).
        """
        if hasattr(action, "tool_calls"):
            return not action.tool_calls
        if hasattr(action, "is_final"):
            return action.is_final
        return False

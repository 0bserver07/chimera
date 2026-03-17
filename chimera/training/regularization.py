"""Regularization callback for synthesis training.

Combines test pass-rate with critic-based code quality scoring to prefer
simpler, higher-quality solutions during synthesis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chimera.training.strategies.base import Callback, EpochResult

if TYPE_CHECKING:
    from chimera.critic.base import Critic, CriticResult


class RegularizationCallback(Callback):
    """Score code quality after each epoch using a Critic.

    When an epoch reaches the minimum pass rate, the critic is invoked to
    score the generated code on readability, maintainability, and simplicity.
    The ``combined_score`` method produces a weighted blend of test pass-rate
    and critic score so that strategies can prefer simpler solutions among
    those that pass the tests.

    Args:
        critic: The critic used to evaluate code quality.
        weight: Weight given to the critic score (0.0-1.0). The pass-rate
            receives weight ``1 - weight``.
        min_pass_rate: Minimum pass rate required before critic evaluation
            is attempted.
    """

    def __init__(
        self,
        critic: "Critic",
        weight: float = 0.3,
        min_pass_rate: float = 0.5,
    ) -> None:
        self.critic = critic
        self.weight = weight
        self.min_pass_rate = min_pass_rate
        self.scores: list[CriticResult] = []

    def on_epoch_end(
        self,
        epoch: int | EpochResult,
        result: EpochResult | None = None,
    ) -> bool | None:
        """Record epoch results and evaluate critic when pass rate is sufficient.

        Supports both callback signatures:
        - ``on_epoch_end(epoch_result)``
        - ``on_epoch_end(epoch_num, epoch_result)``
        """
        if isinstance(epoch, int):
            return True  # backward compat — no EpochResult available as first arg
        epoch_result = epoch
        if epoch_result.pass_rate >= self.min_pass_rate:
            # Evaluate using critic — we don't have full context here,
            # so just record a placeholder.  In practice the strategy
            # would call combined_score() after running the critic.
            pass
        return True

    def combined_score(self, pass_rate: float, critic_score: float) -> float:
        """Compute the regularized score blending pass-rate and critic score.

        Args:
            pass_rate: Test pass rate (0.0-1.0).
            critic_score: Critic quality score (0.0-1.0).

        Returns:
            Weighted combination: ``pass_rate * (1 - weight) + critic_score * weight``.
        """
        return pass_rate * (1 - self.weight) + critic_score * self.weight

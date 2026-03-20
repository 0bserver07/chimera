"""Review orchestrator: manages reviewer-author iteration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from chimera.review.feedback import ReviewFeedback

if TYPE_CHECKING:
    from chimera.core.agent import Agent
    from chimera.env.base import Environment


@dataclass
class ReviewRound:
    """Record of one review-fix cycle."""
    round_number: int
    feedback: ReviewFeedback
    fixed: bool = False


class ReviewOrchestrator:
    """Manages the review-fix iteration cycle.

    Tracks rounds of review feedback and fix attempts,
    determining when the review is complete.
    """

    def __init__(self, max_rounds: int = 3) -> None:
        self._max_rounds = max_rounds
        self._rounds: list[ReviewRound] = []

    @property
    def max_rounds(self) -> int:
        return self._max_rounds

    @property
    def rounds(self) -> list[ReviewRound]:
        return list(self._rounds)

    @property
    def current_round(self) -> int:
        return len(self._rounds)

    @property
    def is_approved(self) -> bool:
        if not self._rounds:
            return False
        return self._rounds[-1].feedback.approved

    @property
    def is_complete(self) -> bool:
        return self.is_approved or self.current_round >= self._max_rounds

    def add_review(self, feedback: ReviewFeedback) -> ReviewRound:
        """Record a review round."""
        round_obj = ReviewRound(
            round_number=self.current_round + 1,
            feedback=feedback,
        )
        self._rounds.append(round_obj)
        return round_obj

    def mark_fixed(self) -> None:
        """Mark the latest round as having been fixed."""
        if self._rounds:
            self._rounds[-1].fixed = True

    def needs_another_round(self) -> bool:
        """Check if another review round is needed."""
        if not self._rounds:
            return True  # No reviews yet
        if self.is_approved:
            return False
        if self.current_round >= self._max_rounds:
            return False
        return True

    def run(
        self,
        diff: str,
        reviewer: Agent,
        author: Agent,
        env: Environment | None = None,
    ) -> bool:
        """Run the review-fix iteration cycle.

        Args:
            diff: The code diff to review.
            reviewer: Agent that reviews the diff and produces feedback.
            author: Agent that fixes issues found by the reviewer.
            env: Optional environment for agents to execute in.

        Returns:
            True if the review is approved, False otherwise.
        """
        while self.needs_another_round():
            review_result = reviewer.run(
                f"Review this diff:\n\n{diff}", env
            )
            feedback = ReviewFeedback.parse_from_text(review_result.output)
            self.add_review(feedback)

            if feedback.approved:
                break

            fix_prompt = "Fix these review comments:\n"
            for comment in feedback.comments:
                fix_prompt += f"- {comment.summary}\n"
            author.run(fix_prompt, env)
            self.mark_fixed()

        return self.is_approved

    @property
    def total_comments(self) -> int:
        return sum(r.feedback.comment_count for r in self._rounds)

"""Reviewer/Chooser: multi-stage solution ranking.

Ported from SWE-Agent's approach. Generates multiple candidate solutions
(patches / responses), then uses a second LLM call to rank them and pick
the best one.

Example::

    chooser = ReviewerChooser(generator=gen_provider, reviewer=rev_provider)
    result = chooser.choose(messages, tools, n=3)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.providers.base import Provider, Response
    from chimera.types import Message

ToolSchema = dict[str, Any]

_REVIEW_PROMPT = """\
You are a code review expert. Below are {n} candidate solutions to a task.
Evaluate each solution for correctness, completeness, and code quality.
Return ONLY the number (1-based) of the best solution.

{candidates}

Best solution number:"""


@dataclass
class RankedResult:
    """Result of a review/choose round.

    Args:
        best: The chosen response.
        best_index: 0-based index of the chosen response.
        all_responses: All candidate responses.
        review_reasoning: The reviewer's raw output.
    """

    best: "Response"
    best_index: int = 0
    all_responses: list["Response"] = field(default_factory=list)
    review_reasoning: str = ""


class ReviewerChooser:
    """Generate multiple candidates and use an LLM reviewer to pick the best.

    Args:
        generator: Provider used to generate candidate solutions.
        reviewer: Provider used to review and rank. Defaults to generator.
        temperature: Sampling temperature for candidate generation.
    """

    def __init__(
        self,
        generator: "Provider",
        reviewer: "Provider | None" = None,
        temperature: float = 0.7,
    ) -> None:
        self._generator = generator
        self._reviewer = reviewer or generator
        self._temperature = temperature

    def generate_candidates(
        self,
        messages: list["Message"],
        tools: list[ToolSchema] | None = None,
        n: int = 3,
    ) -> list["Response"]:
        """Generate N candidate responses.

        Args:
            messages: Conversation messages.
            tools: Optional tool schemas.
            n: Number of candidates to generate.

        Returns:
            List of N responses.
        """
        candidates = []
        for _ in range(n):
            resp = self._generator.complete(
                messages, tools=tools, temperature=self._temperature,
            )
            candidates.append(resp)
        return candidates

    def review(
        self,
        candidates: list["Response"],
    ) -> RankedResult:
        """Use the reviewer LLM to pick the best candidate.

        Args:
            candidates: List of candidate responses to evaluate.

        Returns:
            RankedResult with the chosen best response.
        """
        from chimera.types import Message as Msg

        # Format candidates for the reviewer
        parts = []
        for i, resp in enumerate(candidates, 1):
            content = resp.content or "(no text, tool calls only)"
            parts.append(f"--- Solution {i} ---\n{content}")
        candidates_text = "\n\n".join(parts)

        prompt = _REVIEW_PROMPT.format(n=len(candidates), candidates=candidates_text)
        review_messages = [Msg.user(prompt)]

        review_resp = self._reviewer.complete(
            review_messages, temperature=0.0,
        )

        # Parse the reviewer's choice
        best_idx = self._parse_choice(review_resp.content, len(candidates))

        return RankedResult(
            best=candidates[best_idx],
            best_index=best_idx,
            all_responses=candidates,
            review_reasoning=review_resp.content,
        )

    def choose(
        self,
        messages: list["Message"],
        tools: list[ToolSchema] | None = None,
        n: int = 3,
    ) -> RankedResult:
        """Full pipeline: generate N candidates, then review and choose.

        Args:
            messages: Conversation messages.
            tools: Optional tool schemas.
            n: Number of candidates to generate.

        Returns:
            RankedResult with the best candidate.
        """
        candidates = self.generate_candidates(messages, tools=tools, n=n)
        return self.review(candidates)

    @staticmethod
    def _parse_choice(text: str, n: int) -> int:
        """Extract the 1-based choice number from reviewer output.

        Returns 0-based index. Falls back to 0 if parsing fails.
        """
        import re
        # Look for a standalone number
        numbers = re.findall(r"\b(\d+)\b", text)
        for num_str in numbers:
            num = int(num_str)
            if 1 <= num <= n:
                return num - 1
        return 0  # Fallback to first candidate

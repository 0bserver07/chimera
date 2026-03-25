"""Request complexity classification — pure heuristic, no LLM."""
from __future__ import annotations

import re
from enum import Enum

__all__ = ["Complexity", "RequestClassifier"]

COMPLEX_SIGNALS = frozenset({
    "implement", "create", "build", "refactor", "review", "debug",
    "migrate", "redesign", "architect", "integrate",
})

MULTI_STEP_SIGNALS = frozenset({
    "and also", "then", "first", "after that", "finally",
    "step 1", "step 2", "both", "across", ", and",
})


class Complexity(Enum):
    """Request complexity levels."""

    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class RequestClassifier:
    """Classify request complexity. No LLM call — pure heuristic.

    Classification is deterministic: same input always returns same output.
    """

    def classify(self, request: str) -> Complexity:
        """Classify *request* into a :class:`Complexity` level.

        Rules:
            TRIVIAL: < 10 words AND ends with '?'
            SIMPLE: 0-1 complex signals AND < 30 words
            MODERATE: 1-2 complex signals OR any multi-step signal
            COMPLEX: 2+ complex signals OR (50+ words AND multi-step)

        Args:
            request: The user request text.

        Returns:
            The classified complexity level.
        """
        words = request.split()
        word_count = len(words)
        lower = request.lower()

        # Count complex signals (tokenise to whole words)
        lower_words = set(re.findall(r"[a-z]+", lower))
        complex_count = len(lower_words & COMPLEX_SIGNALS)

        # Check multi-step signals (phrase matching)
        has_multi_step = any(signal in lower for signal in MULTI_STEP_SIGNALS)

        # TRIVIAL: < 10 words AND ends with '?'
        if word_count < 10 and request.rstrip().endswith("?"):
            return Complexity.TRIVIAL

        # COMPLEX: 2+ complex signals OR (complex signal AND multi-step)
        #          OR (50+ words AND multi-step)
        if (
            complex_count >= 2
            or (complex_count >= 1 and has_multi_step)
            or (word_count >= 50 and has_multi_step)
        ):
            return Complexity.COMPLEX

        # MODERATE: 1-2 complex signals OR any multi-step signal
        if complex_count >= 1 or has_multi_step:
            return Complexity.MODERATE

        # SIMPLE: 0-1 complex signals AND < 30 words
        if word_count < 30:
            return Complexity.SIMPLE

        # Default fallback for long requests without signals
        return Complexity.MODERATE

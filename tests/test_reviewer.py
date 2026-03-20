# tests/test_reviewer.py
"""Tests for chimera.core.reviewer — multi-stage solution ranking."""
from unittest.mock import MagicMock, call

from chimera.core.reviewer import RankedResult, ReviewerChooser
from chimera.providers.base import Response
from chimera.types import Message


def _resp(content: str) -> Response:
    return Response(content=content, tool_calls=[], usage={"input_tokens": 5, "output_tokens": 5})


class TestReviewerChooser:
    def test_generate_candidates(self):
        provider = MagicMock()
        provider.complete.side_effect = [_resp("A"), _resp("B"), _resp("C")]
        chooser = ReviewerChooser(generator=provider)
        candidates = chooser.generate_candidates([Message.user("task")], n=3)
        assert len(candidates) == 3
        assert candidates[0].content == "A"

    def test_review_picks_best(self):
        gen = MagicMock()
        rev = MagicMock()
        # Reviewer says solution 2 is best
        rev.complete.return_value = _resp("The best solution is 2.")
        chooser = ReviewerChooser(generator=gen, reviewer=rev)

        candidates = [_resp("sol A"), _resp("sol B"), _resp("sol C")]
        result = chooser.review(candidates)
        assert isinstance(result, RankedResult)
        assert result.best_index == 1
        assert result.best.content == "sol B"

    def test_review_fallback_on_bad_parse(self):
        gen = MagicMock()
        rev = MagicMock()
        rev.complete.return_value = _resp("I can't decide, they're all great!")
        chooser = ReviewerChooser(generator=gen, reviewer=rev)

        candidates = [_resp("A"), _resp("B")]
        result = chooser.review(candidates)
        # Falls back to first candidate
        assert result.best_index == 0

    def test_choose_end_to_end(self):
        gen = MagicMock()
        gen.complete.side_effect = [_resp("X"), _resp("Y"), _resp("Z")]
        rev = MagicMock()
        rev.complete.return_value = _resp("3")
        chooser = ReviewerChooser(generator=gen, reviewer=rev)

        result = chooser.choose([Message.user("do something")], n=3)
        assert result.best.content == "Z"
        assert result.best_index == 2
        assert len(result.all_responses) == 3

    def test_parse_choice_valid(self):
        assert ReviewerChooser._parse_choice("Solution 2 is best", 3) == 1
        assert ReviewerChooser._parse_choice("1", 3) == 0
        assert ReviewerChooser._parse_choice("I choose 3", 3) == 2

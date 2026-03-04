"""Tests for multi-agent code review."""
from __future__ import annotations

import pytest

from chimera.review.feedback import ReviewComment, ReviewFeedback, Severity
from chimera.review.orchestrator import ReviewOrchestrator, ReviewRound


class TestReviewFeedback:
    def test_comment_summary(self):
        c = ReviewComment(file="src/main.py", line=42, severity=Severity.ERROR, message="unused var")
        assert "src/main.py:42" in c.summary
        assert "ERROR" in c.summary

    def test_has_critical(self):
        fb = ReviewFeedback(comments=[
            ReviewComment(file="a.py", severity=Severity.CRITICAL, message="sql injection"),
        ])
        assert fb.has_critical

    def test_no_critical(self):
        fb = ReviewFeedback(comments=[
            ReviewComment(file="a.py", severity=Severity.WARNING, message="style"),
        ])
        assert not fb.has_critical

    def test_has_errors(self):
        fb = ReviewFeedback(comments=[
            ReviewComment(file="a.py", severity=Severity.ERROR, message="bug"),
        ])
        assert fb.has_errors

    def test_by_severity(self):
        fb = ReviewFeedback(comments=[
            ReviewComment(file="a.py", severity=Severity.WARNING, message="w1"),
            ReviewComment(file="b.py", severity=Severity.ERROR, message="e1"),
            ReviewComment(file="c.py", severity=Severity.WARNING, message="w2"),
        ])
        assert len(fb.by_severity(Severity.WARNING)) == 2
        assert len(fb.by_severity(Severity.ERROR)) == 1

    def test_by_file(self):
        fb = ReviewFeedback(comments=[
            ReviewComment(file="a.py", message="m1"),
            ReviewComment(file="b.py", message="m2"),
            ReviewComment(file="a.py", message="m3"),
        ])
        assert len(fb.by_file("a.py")) == 2

    def test_files_reviewed(self):
        fb = ReviewFeedback(comments=[
            ReviewComment(file="a.py"),
            ReviewComment(file="b.py"),
            ReviewComment(file="a.py"),
        ])
        assert fb.files_reviewed == ["a.py", "b.py"]

    def test_parse_from_text(self):
        text = """
        [ERROR] src/main.py:42: unused variable x
        [WARNING] src/utils.py: consider refactoring
        [SUGGESTION] src/api.py:10: add docstring

        Overall: approved
        """
        fb = ReviewFeedback.parse_from_text(text)
        assert len(fb.comments) == 3
        assert fb.comments[0].severity == Severity.ERROR
        assert fb.comments[0].line == 42

    def test_parse_approved(self):
        text = "[SUGGESTION] a.py: minor style\nApproved with minor suggestions."
        fb = ReviewFeedback.parse_from_text(text)
        assert fb.approved

    def test_parse_not_approved_with_errors(self):
        text = "[ERROR] a.py: critical bug\nApproved."
        fb = ReviewFeedback.parse_from_text(text)
        assert not fb.approved  # has errors, so not approved


class TestReviewOrchestrator:
    def test_initial_state(self):
        orch = ReviewOrchestrator()
        assert orch.current_round == 0
        assert not orch.is_approved
        assert not orch.is_complete
        assert orch.needs_another_round()

    def test_approved_after_review(self):
        orch = ReviewOrchestrator()
        fb = ReviewFeedback(approved=True)
        orch.add_review(fb)
        assert orch.is_approved
        assert orch.is_complete
        assert not orch.needs_another_round()

    def test_max_rounds_reached(self):
        orch = ReviewOrchestrator(max_rounds=2)
        orch.add_review(ReviewFeedback())
        orch.add_review(ReviewFeedback())
        assert orch.is_complete
        assert not orch.needs_another_round()

    def test_mark_fixed(self):
        orch = ReviewOrchestrator()
        orch.add_review(ReviewFeedback(comments=[ReviewComment(file="a.py")]))
        orch.mark_fixed()
        assert orch.rounds[-1].fixed

    def test_total_comments(self):
        orch = ReviewOrchestrator()
        orch.add_review(ReviewFeedback(comments=[
            ReviewComment(file="a.py"),
            ReviewComment(file="b.py"),
        ]))
        orch.add_review(ReviewFeedback(comments=[
            ReviewComment(file="c.py"),
        ]))
        assert orch.total_comments == 3

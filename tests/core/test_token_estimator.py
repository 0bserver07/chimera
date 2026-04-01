"""Tests for chimera.core.token_estimator — Phase 5."""
from __future__ import annotations

import pytest

from chimera.core.token_estimator import TokenEstimator


class TestTokenEstimator:
    """TokenEstimator fast estimation and API fallback."""

    def test_estimate_text(self):
        estimator = TokenEstimator()
        text = "Hello, world! This is a test."
        result = estimator.estimate(text)
        # len(text) // 4
        assert result == len(text) // 4

    @pytest.mark.asyncio
    async def test_count_messages_returns_none_without_provider(self):
        estimator = TokenEstimator()
        messages = [{"role": "user", "content": "hi"}]
        result = await estimator.count_messages(messages)
        assert result is None

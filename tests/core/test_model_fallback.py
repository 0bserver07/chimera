"""Tests for chimera.core.model_fallback — Fallback Model Switching."""
from __future__ import annotations

from chimera.core.model_fallback import ModelFallbackConfig, ModelFallbackManager


class TestShouldFallback:
    """should_fallback returns True for configured error codes."""

    def test_429_triggers_fallback(self):
        config = ModelFallbackConfig(
            primary_model="claude-opus-4-20250514",
            fallback_model="claude-sonnet-4-20250514",
        )
        mgr = ModelFallbackManager(config)
        assert mgr.should_fallback(429) is True

    def test_529_triggers_fallback(self):
        config = ModelFallbackConfig(
            primary_model="claude-opus-4-20250514",
            fallback_model="claude-sonnet-4-20250514",
        )
        mgr = ModelFallbackManager(config)
        assert mgr.should_fallback(529) is True

    def test_500_does_not_trigger(self):
        config = ModelFallbackConfig(
            primary_model="claude-opus-4-20250514",
            fallback_model="claude-sonnet-4-20250514",
        )
        mgr = ModelFallbackManager(config)
        assert mgr.should_fallback(500) is False

    def test_429_disabled(self):
        config = ModelFallbackConfig(
            primary_model="claude-opus-4-20250514",
            fallback_model="claude-sonnet-4-20250514",
            fallback_on_429=False,
        )
        mgr = ModelFallbackManager(config)
        assert mgr.should_fallback(429) is False

    def test_529_disabled(self):
        config = ModelFallbackConfig(
            primary_model="claude-opus-4-20250514",
            fallback_model="claude-sonnet-4-20250514",
            fallback_on_529=False,
        )
        mgr = ModelFallbackManager(config)
        assert mgr.should_fallback(529) is False

    def test_no_fallback_model_configured(self):
        config = ModelFallbackConfig(primary_model="claude-opus-4-20250514")
        mgr = ModelFallbackManager(config)
        assert mgr.should_fallback(429) is False


class TestMaxAttempts:
    """Fallback is refused once max_fallback_attempts is reached."""

    def test_max_attempts_exhausted(self):
        config = ModelFallbackConfig(
            primary_model="claude-opus-4-20250514",
            fallback_model="claude-sonnet-4-20250514",
            max_fallback_attempts=2,
        )
        mgr = ModelFallbackManager(config)
        mgr.activate_fallback()
        mgr.activate_fallback()
        assert mgr.should_fallback(429) is False

    def test_under_max_attempts(self):
        config = ModelFallbackConfig(
            primary_model="claude-opus-4-20250514",
            fallback_model="claude-sonnet-4-20250514",
            max_fallback_attempts=3,
        )
        mgr = ModelFallbackManager(config)
        mgr.activate_fallback()
        mgr.activate_fallback()
        assert mgr.should_fallback(429) is True


class TestActivateFallback:
    """activate_fallback switches current_model and returns fallback name."""

    def test_returns_fallback_model(self):
        config = ModelFallbackConfig(
            primary_model="claude-opus-4-20250514",
            fallback_model="claude-sonnet-4-20250514",
        )
        mgr = ModelFallbackManager(config)
        result = mgr.activate_fallback()
        assert result == "claude-sonnet-4-20250514"

    def test_current_model_switches(self):
        config = ModelFallbackConfig(
            primary_model="claude-opus-4-20250514",
            fallback_model="claude-sonnet-4-20250514",
        )
        mgr = ModelFallbackManager(config)
        assert mgr.current_model == "claude-opus-4-20250514"
        mgr.activate_fallback()
        assert mgr.current_model == "claude-sonnet-4-20250514"


class TestReset:
    """reset restores the primary model."""

    def test_reset_restores_primary(self):
        config = ModelFallbackConfig(
            primary_model="claude-opus-4-20250514",
            fallback_model="claude-sonnet-4-20250514",
        )
        mgr = ModelFallbackManager(config)
        mgr.activate_fallback()
        assert mgr.current_model == "claude-sonnet-4-20250514"
        mgr.reset()
        assert mgr.current_model == "claude-opus-4-20250514"

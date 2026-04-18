"""Tests for chimera.core.feature_flags — FeatureFlags class-level state."""
from __future__ import annotations


import pytest

from chimera.core.feature_flags import FeatureFlags


class TestFeatureFlags:
    """Five tests covering FeatureFlags behaviour."""

    def setup_method(self) -> None:
        FeatureFlags.reset()

    def teardown_method(self) -> None:
        FeatureFlags.reset()

    def test_default_disabled(self) -> None:
        """Unknown flags default to False."""
        assert not FeatureFlags.enabled("NONEXISTENT_FLAG")

    def test_set_and_check(self) -> None:
        """set() makes enabled() return True."""
        FeatureFlags.set("MY_FLAG", True)
        assert FeatureFlags.enabled("MY_FLAG")

    def test_override_wins(self) -> None:
        """Runtime override beats the base flag value."""
        FeatureFlags.set("MY_FLAG", False)
        FeatureFlags.override("MY_FLAG", True)
        assert FeatureFlags.enabled("MY_FLAG")

        # Override can also disable a flag that was set to True
        FeatureFlags.set("ANOTHER", True)
        FeatureFlags.override("ANOTHER", False)
        assert not FeatureFlags.enabled("ANOTHER")

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() reads CHIMERA_FEATURE_* environment variables."""
        monkeypatch.setenv("CHIMERA_FEATURE_COORDINATOR_MODE", "1")
        monkeypatch.setenv("CHIMERA_FEATURE_BETA_UI", "true")
        monkeypatch.setenv("CHIMERA_FEATURE_OFF_FLAG", "0")
        FeatureFlags.from_env()
        assert FeatureFlags.enabled("COORDINATOR_MODE")
        assert FeatureFlags.enabled("BETA_UI")
        assert not FeatureFlags.enabled("OFF_FLAG")

    def test_reset(self) -> None:
        """reset() clears both flags and overrides."""
        FeatureFlags.set("A", True)
        FeatureFlags.override("B", True)
        FeatureFlags.reset()
        assert not FeatureFlags.enabled("A")
        assert not FeatureFlags.enabled("B")

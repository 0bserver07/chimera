"""Tests for chimera.coordinator.mode — CoordinatorMode dispatch."""
from __future__ import annotations

import pytest

from chimera.core.feature_flags import FeatureFlags
from chimera.coordinator.mode import CoordinatorMode


class TestCoordinatorMode:

    def setup_method(self) -> None:
        FeatureFlags.reset()

    def teardown_method(self) -> None:
        FeatureFlags.reset()

    def test_disabled_by_default(self) -> None:
        """CoordinatorMode.is_enabled is False when the flag is not set."""
        coord = CoordinatorMode(spawner=None, agent_definitions={})
        assert not coord.is_enabled

    def test_enabled_with_flag(self) -> None:
        """CoordinatorMode.is_enabled is True when the flag is set."""
        FeatureFlags.set("COORDINATOR_MODE", True)
        coord = CoordinatorMode(spawner=None, agent_definitions={})
        assert coord.is_enabled

"""Tests for chimera.core.auto_background — IG-4."""
from __future__ import annotations

import pytest

from chimera.core.auto_background import AutoBackgroundConfig, AutoBackgroundMonitor


class TestAutoBackgroundConfig:
    """AutoBackgroundConfig stores threshold and enabled state."""

    def test_defaults(self):
        cfg = AutoBackgroundConfig()
        assert cfg.threshold_ms == 120_000
        assert cfg.enabled is True

    def test_custom_threshold(self):
        cfg = AutoBackgroundConfig(threshold_ms=60_000)
        assert cfg.threshold_ms == 60_000


class TestAutoBackgroundMonitor:
    """AutoBackgroundMonitor decides when to background a long-running task."""

    @pytest.mark.asyncio
    async def test_should_background_when_threshold_exceeded(self):
        monitor = AutoBackgroundMonitor()
        assert await monitor.should_background(130_000) is True

    @pytest.mark.asyncio
    async def test_should_not_background_below_threshold(self):
        monitor = AutoBackgroundMonitor()
        assert await monitor.should_background(60_000) is False

    @pytest.mark.asyncio
    async def test_should_not_background_when_disabled(self):
        config = AutoBackgroundConfig(enabled=False)
        monitor = AutoBackgroundMonitor(config)
        assert await monitor.should_background(200_000) is False

    @pytest.mark.asyncio
    async def test_exact_threshold_triggers(self):
        monitor = AutoBackgroundMonitor()
        assert await monitor.should_background(120_000) is True

    @pytest.mark.asyncio
    async def test_custom_config(self):
        config = AutoBackgroundConfig(threshold_ms=5_000, enabled=True)
        monitor = AutoBackgroundMonitor(config)
        assert await monitor.should_background(5_000) is True
        assert await monitor.should_background(4_999) is False

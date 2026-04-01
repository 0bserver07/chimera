"""AutoBackgroundMonitor: decide when to move long-running tasks to background.

Provides :class:`AutoBackgroundConfig` for threshold/enable settings and
:class:`AutoBackgroundMonitor` which checks elapsed time against the threshold.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AutoBackgroundConfig:
    """Configuration for automatic background promotion.

    Attributes:
        threshold_ms: Elapsed milliseconds before a task should be backgrounded.
        enabled: Whether automatic backgrounding is active.
    """

    threshold_ms: int = 120_000  # 2 minutes
    enabled: bool = True


class AutoBackgroundMonitor:
    """Monitors elapsed time and decides whether to background a task.

    Args:
        config: Configuration controlling threshold and enabled state.
            Defaults to :class:`AutoBackgroundConfig` with default values.
    """

    def __init__(self, config: AutoBackgroundConfig | None = None) -> None:
        self.config = config or AutoBackgroundConfig()

    async def should_background(self, elapsed_ms: float) -> bool:
        """Return True if the task should be moved to background.

        Args:
            elapsed_ms: How long the task has been running, in milliseconds.

        Returns:
            ``True`` if backgrounding is enabled and the threshold is met.
        """
        return self.config.enabled and elapsed_ms >= self.config.threshold_ms

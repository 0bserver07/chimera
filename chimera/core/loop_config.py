"""LoopConfig: optional configuration injected into all loop variants."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.compaction.base import CompactionStrategy
    from chimera.detection.actions import LoopDetector
    from chimera.events.base import EventBus
    from chimera.permissions.base import PermissionPolicy
    from chimera.streaming.base import StreamHandler

__all__ = ["LoopConfig"]


@dataclass
class LoopConfig:
    """Optional behaviour injections for any loop variant.

    All fields default to ``None``, meaning the loop behaves exactly as
    before (pure ReAct with no permissions, detection, compaction, events,
    or streaming).  Set any field to enable that behaviour.

    Example::

        config = LoopConfig(
            permissions=PermissionRuleset(rules=[...]),
            detector=LoopDetector(on_detect=OnDetect.WARN),
            handler=ConsoleStreamHandler(),
            event_bus=EventBus(),
        )
        loop = ReAct(max_steps=50, config=config)
    """

    permissions: PermissionPolicy | None = None
    detector: LoopDetector | None = None
    compaction: CompactionStrategy | None = None
    handler: StreamHandler | None = None
    event_bus: EventBus | None = None
    auto_compact_threshold: float = 0.8

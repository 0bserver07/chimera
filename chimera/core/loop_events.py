from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LoopEventType(Enum):
    stream_start = "stream_start"
    assistant = "assistant"
    assistant_chunk = "assistant_chunk"
    thinking_chunk = "thinking_chunk"
    tool_use = "tool_use"
    tool_progress = "tool_progress"
    tool_result = "tool_result"
    system = "system"
    compact_boundary = "compact_boundary"
    error = "error"
    result = "result"


@dataclass
class LoopEvent:
    type: LoopEventType
    data: Any
    turn: int
    timestamp: float = field(default_factory=time.time)


@dataclass
class LoopResult:
    reason: str
    messages: list[Any]
    usage: dict[str, Any]
    cost_usd: float
    duration_ms: float
    turn_count: int

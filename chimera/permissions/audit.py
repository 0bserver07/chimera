"""Permission audit logging."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class AuditEntry:
    """Record of a single permission decision."""
    timestamp: float
    tool_name: str
    arguments: dict
    decision: str  # "approved", "denied", "auto_approved", "auto_denied"
    reason: str = ""

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))


class AuditLog:
    """Collects permission decision records."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def record(self, tool_name: str, arguments: dict, decision: str, reason: str = "") -> None:
        self._entries.append(AuditEntry(
            timestamp=time.time(),
            tool_name=tool_name,
            arguments=arguments,
            decision=decision,
            reason=reason,
        ))

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._entries:
            counts[entry.decision] = counts.get(entry.decision, 0) + 1
        return counts

    def for_tool(self, tool_name: str) -> list[AuditEntry]:
        return [e for e in self._entries if e.tool_name == tool_name]

    def clear(self) -> None:
        self._entries.clear()

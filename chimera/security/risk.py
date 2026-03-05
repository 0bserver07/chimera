"""Security risk levels for tool call analysis."""
from __future__ import annotations

from enum import IntEnum

__all__ = ["SecurityRisk"]


class SecurityRisk(IntEnum):
    """Risk level assigned to a tool call by a security analyzer.

    UNKNOWN is treated as HIGH for safety when comparing risk levels.
    """

    UNKNOWN = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    def is_riskier_than(self, other: SecurityRisk) -> bool:
        """Compare risk levels. UNKNOWN is treated as HIGH for safety."""
        self_val = 3 if self == SecurityRisk.UNKNOWN else self.value
        other_val = 3 if other == SecurityRisk.UNKNOWN else other.value
        return self_val > other_val

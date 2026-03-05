"""Confirmation policies for security risk decisions."""
from __future__ import annotations

from abc import ABC, abstractmethod

from chimera.security.risk import SecurityRisk

__all__ = [
    "ConfirmationPolicy",
    "NeverConfirm",
    "AlwaysConfirm",
    "ConfirmAboveThreshold",
]


class ConfirmationPolicy(ABC):
    """Abstract base for confirmation policies."""

    @abstractmethod
    def should_confirm(self, risk: SecurityRisk) -> bool:
        """Return True if the given risk level requires confirmation."""
        ...


class NeverConfirm(ConfirmationPolicy):
    """Never require confirmation regardless of risk."""

    def should_confirm(self, risk: SecurityRisk) -> bool:
        return False


class AlwaysConfirm(ConfirmationPolicy):
    """Always require confirmation regardless of risk."""

    def should_confirm(self, risk: SecurityRisk) -> bool:
        return True


class ConfirmAboveThreshold(ConfirmationPolicy):
    """Require confirmation when risk meets or exceeds the threshold.

    Args:
        threshold: Minimum risk level that triggers confirmation.
        confirm_unknown: Whether UNKNOWN risk requires confirmation.
    """

    def __init__(
        self,
        threshold: SecurityRisk = SecurityRisk.MEDIUM,
        confirm_unknown: bool = True,
    ) -> None:
        self.threshold = threshold
        self.confirm_unknown = confirm_unknown

    def should_confirm(self, risk: SecurityRisk) -> bool:
        if risk == SecurityRisk.UNKNOWN:
            return self.confirm_unknown
        return risk >= self.threshold

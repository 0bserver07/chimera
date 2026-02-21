# chimera/core/approval.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ApprovalPolicy(ABC):
    """Decides whether a tool invocation should proceed."""

    @abstractmethod
    def should_approve(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Return True to allow, False to deny."""


class AutoApprove(ApprovalPolicy):
    """Approve everything. Default for non-interactive use."""

    def should_approve(self, tool_name: str, args: dict[str, Any]) -> bool:
        return True


class AlwaysDeny(ApprovalPolicy):
    """Deny everything. Useful for dry-run mode."""

    def should_approve(self, tool_name: str, args: dict[str, Any]) -> bool:
        return False


class AllowList(ApprovalPolicy):
    """Only approve tools on the allow list."""

    def __init__(self, allowed: list[str]) -> None:
        self._allowed = set(allowed)

    def should_approve(self, tool_name: str, args: dict[str, Any]) -> bool:
        return tool_name in self._allowed

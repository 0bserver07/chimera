# chimera/permissions/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

__all__ = ["PermissionAction", "PermissionPolicy"]


class PermissionAction(Enum):
    """The three possible outcomes of a permission evaluation."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionPolicy(ABC):
    """Decides whether a tool invocation should be allowed, denied, or require user input."""

    @abstractmethod
    def evaluate(self, tool_name: str, args: dict[str, Any]) -> PermissionAction:
        """Return the permission action for the given tool invocation."""

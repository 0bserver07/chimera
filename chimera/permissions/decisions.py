"""Permission decision types returned by the checker."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chimera.permissions.rules import PermissionBehavior

__all__ = ["DecisionReason", "PermissionDecision"]


@dataclass
class DecisionReason:
    """Machine-readable explanation of *why* a decision was made.

    Use the classmethods for convenient construction.
    """

    type: str
    detail: Any = None

    @classmethod
    def rule(cls, detail: Any = None) -> DecisionReason:
        """Decision was driven by a matching permission rule."""
        return cls(type="rule", detail=detail)

    @classmethod
    def mode(cls, detail: Any = None) -> DecisionReason:
        """Decision was driven by the active permission mode."""
        return cls(type="mode", detail=detail)


@dataclass
class PermissionDecision:
    """The outcome of a permission check for a single tool invocation.

    Attributes:
        behavior:      ALLOW / DENY / ASK.
        message:       Human-readable explanation.
        reason:        Structured reason (optional).
        suggestions:   List of suggested rules the user could add (optional).
        updated_input: Modified tool input to enforce safety (optional).
    """

    behavior: PermissionBehavior
    message: str
    reason: DecisionReason | None = None
    suggestions: list[str] | None = None
    updated_input: dict[str, Any] | None = None

    # ----- convenience constructors -----------------------------------------

    @classmethod
    def allow(
        cls,
        message: str = "",
        *,
        reason: DecisionReason | None = None,
        updated_input: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        return cls(
            behavior=PermissionBehavior.ALLOW,
            message=message,
            reason=reason,
            updated_input=updated_input,
        )

    @classmethod
    def deny(
        cls,
        message: str = "",
        *,
        reason: DecisionReason | None = None,
    ) -> PermissionDecision:
        return cls(
            behavior=PermissionBehavior.DENY,
            message=message,
            reason=reason,
        )

    @classmethod
    def ask(
        cls,
        message: str = "",
        *,
        reason: DecisionReason | None = None,
        suggestions: list[str] | None = None,
    ) -> PermissionDecision:
        return cls(
            behavior=PermissionBehavior.ASK,
            message=message,
            reason=reason,
            suggestions=suggestions,
        )

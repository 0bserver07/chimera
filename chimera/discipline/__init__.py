"""Workflow discipline: phase gates, scope guards, instruction anchoring.

Structural constraints that keep agents focused.  Phase gates enforce
workflow order, scope guards detect drift, depth guards prevent rabbit
holes, and instruction anchors combat context degradation.
"""
from __future__ import annotations

from chimera.discipline.anchor import InstructionAnchor
from chimera.discipline.guard import (
    DepthGuard,
    DisciplineGuard,
    DisciplineViolation,
    GuardResult,
    RetryBudgetGuard,
    ScopeGuard,
    VerificationGuard,
)
from chimera.discipline.patterns import (
    BOUNDED_EXPLORATION,
    BOUNDED_RETRY,
    SCOPE_ONLY,
    STRICT,
    VERIFY_FIRST,
    DisciplinePattern,
)
from chimera.discipline.phase import Gate, Phase, PhasedWorkflow

__all__ = [
    # Phase workflow
    "Gate",
    "Phase",
    "PhasedWorkflow",
    # Guards
    "DisciplineGuard",
    "DisciplineViolation",
    "DepthGuard",
    "GuardResult",
    "RetryBudgetGuard",
    "ScopeGuard",
    "VerificationGuard",
    # Anchor
    "InstructionAnchor",
    # Patterns
    "DisciplinePattern",
    "BOUNDED_EXPLORATION",
    "BOUNDED_RETRY",
    "SCOPE_ONLY",
    "STRICT",
    "VERIFY_FIRST",
]

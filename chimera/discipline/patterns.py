"""Pre-built discipline configurations.

Compose freely by concatenating lists::

    my_guards = SCOPE_ONLY + VERIFY_FIRST
"""
from __future__ import annotations

from chimera.discipline.guard import (
    DepthGuard,
    DisciplineGuard,
    RetryBudgetGuard,
    ScopeGuard,
    VerificationGuard,
)

__all__ = [
    "BOUNDED_EXPLORATION",
    "BOUNDED_RETRY",
    "DisciplinePattern",
    "SCOPE_ONLY",
    "STRICT",
    "VERIFY_FIRST",
]

# Type alias for documentation clarity.
DisciplinePattern = list[DisciplineGuard]

SCOPE_ONLY: DisciplinePattern = [ScopeGuard()]
VERIFY_FIRST: DisciplinePattern = [VerificationGuard()]
BOUNDED_RETRY: DisciplinePattern = [RetryBudgetGuard(max_retries=3)]
BOUNDED_EXPLORATION: DisciplinePattern = [DepthGuard(max_depth=10)]

STRICT: DisciplinePattern = [
    ScopeGuard(),
    VerificationGuard(),
    RetryBudgetGuard(max_retries=3),
    DepthGuard(max_depth=10),
]

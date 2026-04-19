"""LoopDeps: dependency injection container for the agent loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

__all__ = ["LoopDeps", "production_deps"]


@dataclass
class LoopDeps:
    """Minimal set of dependencies required by any loop variant.

    Fields
    ------
    call_model:
        Provider call function — accepts messages, returns a response.
    compact:
        Context compaction function — reduces the message history when the
        context window is near capacity.
    uuid:
        Zero-argument callable that returns a fresh UUID string.  Defaults
        to a ``uuid4``-based generator; override in tests for determinism.
    """

    call_model: Callable[..., Any]
    compact: Callable[..., Any]
    uuid: Callable[[], str] = field(default_factory=lambda: lambda: str(uuid4()))


def production_deps(provider, compactor) -> LoopDeps:
    """Build a :class:`LoopDeps` from production-ready objects.

    Parameters
    ----------
    provider:
        An object with a ``call`` (or equivalent) method used as
        ``call_model``.
    compactor:
        An object with a ``compact`` (or equivalent) method used as
        ``compact``.

    Returns
    -------
    LoopDeps
        Ready-to-use dependency container wired to real services.
    """
    return LoopDeps(
        call_model=provider,
        compact=compactor,
    )

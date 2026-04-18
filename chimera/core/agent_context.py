"""AgentContext: isolated execution context for sub-agents.

Provides :class:`IsolationLevel` to control how much state a child agent
shares with its parent, and :class:`AgentContext` which bundles all
per-agent runtime state (messages, caches, abort signal, callbacks).
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from chimera.core.abort import AbortSignal
from chimera.core.loop_state import QuerySource

__all__ = ["AgentContext", "IsolationLevel"]


class IsolationLevel(Enum):
    """How much state a child agent inherits from its parent."""

    FULL = "full"           # Clone everything; set_app_state is no-op
    SELECTIVE = "selective"  # Clone data but share set_app_state
    SHARED = "shared"        # Share set_app_state (data still cloned)


@dataclass
class AgentContext:
    """Runtime context for a single agent invocation.

    Contains the agent's message history, file caches, abort signal, and
    callbacks for reading/writing shared application state.
    """

    messages: list[Any]
    file_state_cache: Any  # FileStateCache or dict[str, Any]
    abort_signal: AbortSignal
    denial_tracking: Any  # DenialTrackingState or dict[str, Any]
    agent_id: str
    parent_agent_id: str | None
    query_source: QuerySource
    depth: int

    # Callbacks for application state management
    get_app_state: Callable[[], Any] = field(repr=False)
    set_app_state: Callable[[Any], None] = field(repr=False)
    set_app_state_for_tasks: Callable[[Any], None] = field(repr=False)

    @classmethod
    def create_child(
        cls,
        parent: AgentContext,
        *,
        isolation: IsolationLevel = IsolationLevel.FULL,
        share_abort: bool = False,
    ) -> AgentContext:
        """Create an isolated child context from *parent*.

        Args:
            parent: The parent context to derive from.
            isolation: Controls how much state is shared with the parent.
            share_abort: If ``True``, the child's abort signal is linked
                to the parent's (parent abort cascades to child). If ``False``,
                the child gets a completely independent abort signal.

        Returns:
            A new :class:`AgentContext` with cloned state and fresh
            denial tracking.
        """
        # Abort signal: linked child or independent
        if share_abort:
            child_abort = parent.abort_signal.linked_child()
        else:
            child_abort = AbortSignal()

        # set_app_state: depends on isolation level
        if isolation == IsolationLevel.FULL:
            def _noop_set_app_state(updater: Any) -> None:
                return None
            child_set_app_state: Callable[[Any], None] = _noop_set_app_state
        else:
            # SELECTIVE and SHARED both share set_app_state with parent
            child_set_app_state = parent.set_app_state

        # Clone file_state_cache: prefer .clone() if available, else copy.copy()
        fsc = parent.file_state_cache
        if hasattr(fsc, "clone"):
            child_fsc = fsc.clone()
        else:
            child_fsc = copy.copy(fsc)

        # Fresh denial tracking: try DenialTrackingState() if importable, else {}
        try:
            from chimera.core.denial_tracking import DenialTrackingState
            child_denial: Any = DenialTrackingState()
        except (ImportError, AttributeError):
            child_denial = {}

        return cls(
            messages=[],
            file_state_cache=child_fsc,
            abort_signal=child_abort,
            denial_tracking=child_denial,
            agent_id=str(uuid.uuid4()),
            parent_agent_id=parent.agent_id,
            query_source=parent.query_source,
            depth=parent.depth + 1,
            get_app_state=parent.get_app_state,
            set_app_state=child_set_app_state,
            # set_app_state_for_tasks ALWAYS uses parent's callback
            set_app_state_for_tasks=parent.set_app_state_for_tasks,
        )

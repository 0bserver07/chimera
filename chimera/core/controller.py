"""AgentController: FSM-based lifecycle management for complex agent workflows.

States: INIT → PLANNING → EXECUTING → WAITING → REVIEWING → DONE / ERROR

Each state has entry/exit hooks. Transitions are triggered explicitly or by events.
State is serializable for persistence.

Inspired by OpenHands' AgentController pattern.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class AgentState(Enum):
    """Agent lifecycle states."""

    INIT = "init"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING = "waiting"
    REVIEWING = "reviewing"
    DONE = "done"
    ERROR = "error"


# Valid transitions: from_state → set of allowed to_states
_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.INIT: {AgentState.PLANNING, AgentState.EXECUTING, AgentState.ERROR},
    AgentState.PLANNING: {AgentState.EXECUTING, AgentState.ERROR, AgentState.DONE},
    AgentState.EXECUTING: {AgentState.WAITING, AgentState.REVIEWING, AgentState.DONE, AgentState.ERROR},
    AgentState.WAITING: {AgentState.EXECUTING, AgentState.REVIEWING, AgentState.ERROR},
    AgentState.REVIEWING: {AgentState.EXECUTING, AgentState.PLANNING, AgentState.DONE, AgentState.ERROR},
    AgentState.DONE: set(),
    AgentState.ERROR: {AgentState.INIT},  # Can reset from error
}

StateHook = Callable[["AgentController"], None]


@dataclass
class StateTransition:
    """Record of a state transition."""

    from_state: AgentState
    to_state: AgentState
    timestamp: float
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentController:
    """FSM-based agent lifecycle controller.

    Example::

        ctrl = AgentController()
        ctrl.on_enter(AgentState.PLANNING, lambda c: print("Planning started"))
        ctrl.transition_to(AgentState.PLANNING)
        ctrl.transition_to(AgentState.EXECUTING)
        ctrl.transition_to(AgentState.DONE)
        print(ctrl.history)  # All transitions
    """

    def __init__(self, initial_state: AgentState = AgentState.INIT) -> None:
        self._state = initial_state
        self._history: list[StateTransition] = []
        self._enter_hooks: dict[AgentState, list[StateHook]] = {}
        self._exit_hooks: dict[AgentState, list[StateHook]] = {}
        self._metadata: dict[str, Any] = {}

    @property
    def state(self) -> AgentState:
        """Current state."""
        return self._state

    @property
    def history(self) -> list[StateTransition]:
        """All state transitions that have occurred."""
        return list(self._history)

    @property
    def is_terminal(self) -> bool:
        """Whether the controller is in a terminal state (DONE or ERROR)."""
        return self._state in (AgentState.DONE, AgentState.ERROR)

    @property
    def metadata(self) -> dict[str, Any]:
        """Arbitrary metadata attached to the controller."""
        return self._metadata

    def on_enter(self, state: AgentState, hook: StateHook) -> None:
        """Register a hook to run when entering a state."""
        self._enter_hooks.setdefault(state, []).append(hook)

    def on_exit(self, state: AgentState, hook: StateHook) -> None:
        """Register a hook to run when exiting a state."""
        self._exit_hooks.setdefault(state, []).append(hook)

    def can_transition_to(self, target: AgentState) -> bool:
        """Check if a transition to *target* is valid from current state."""
        return target in _TRANSITIONS.get(self._state, set())

    def transition_to(self, target: AgentState, **meta: Any) -> None:
        """Transition to a new state.

        Args:
            target: The state to transition to.
            **meta: Optional metadata to record with the transition.

        Raises:
            ValueError: If the transition is not valid.
        """
        if not self.can_transition_to(target):
            raise ValueError(
                f"Invalid transition: {self._state.value} → {target.value}. "
                f"Allowed: {', '.join(s.value for s in _TRANSITIONS.get(self._state, set()))}"
            )

        # Exit hooks for current state
        for hook in self._exit_hooks.get(self._state, []):
            hook(self)

        prev = self._state
        self._state = target
        self._history.append(StateTransition(
            from_state=prev,
            to_state=target,
            timestamp=time.monotonic(),
            metadata=dict(meta),
        ))

        # Enter hooks for new state
        for hook in self._enter_hooks.get(target, []):
            hook(self)

    def to_dict(self) -> dict[str, Any]:
        """Serialize controller state for persistence."""
        return {
            "state": self._state.value,
            "history": [
                {
                    "from": t.from_state.value,
                    "to": t.to_state.value,
                    "timestamp": t.timestamp,
                    "metadata": t.metadata,
                }
                for t in self._history
            ],
            "metadata": self._metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentController:
        """Restore controller from serialized state."""
        ctrl = cls(initial_state=AgentState(data["state"]))
        ctrl._metadata = data.get("metadata", {})
        for t in data.get("history", []):
            ctrl._history.append(StateTransition(
                from_state=AgentState(t["from"]),
                to_state=AgentState(t["to"]),
                timestamp=t["timestamp"],
                metadata=t.get("metadata", {}),
            ))
        return ctrl

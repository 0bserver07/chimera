"""Projector ABC and registry with transactional execution.

A projector consumes typed events (already deserialized from the store)
and folds them into application state — e.g. a session-state dict, a
search index, or a metrics aggregator.

The :class:`ProjectorRegistry` tracks per-projector cursors (``last
seq``) so :meth:`replay` can resume after a crash without re-applying
already-folded events.  Each batch is *transactional*: when a projector
raises, its cursor is **not** advanced and the exception is re-raised so
the caller can decide whether to skip the bad event or abort.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterable

__all__ = [
    "Projector",
    "ProjectorRegistry",
    "ProjectorState",
    "ProjectorError",
]


class ProjectorError(Exception):
    """Raised by :meth:`ProjectorRegistry.replay` when a projector aborts.

    The original exception is set as ``__cause__``.
    """

    def __init__(self, projector_name: str, seq: int, original: BaseException) -> None:
        self.projector_name = projector_name
        self.seq = seq
        super().__init__(
            f"Projector {projector_name!r} failed on seq={seq}: {original}",
        )


class Projector(ABC):
    """Base class for event projectors.

    Subclasses override :meth:`apply` (and optionally :meth:`reset`) to
    fold typed events into derived state.  The registry handles
    cursor bookkeeping; projectors are pure functions over their own
    state.
    """

    name: str = ""

    @abstractmethod
    def apply(self, event_name: str, payload: Any) -> None:
        """Fold one event into projector state.

        Args:
            event_name: Logical event name (without version suffix).
            payload: The deserialized event payload (typed dataclass, or
                a dict if the registry could not resolve a class).

        Raises:
            Exception: Any exception aborts the current
                :meth:`ProjectorRegistry.replay` batch and prevents the
                cursor from advancing.
        """

    def reset(self) -> None:  # pragma: no cover — default no-op
        """Reset projector state.  Override when state is non-trivial."""


class ProjectorState:
    """Snapshot of one projector's cursor inside :class:`ProjectorRegistry`."""

    __slots__ = ("name", "last_seq")

    def __init__(self, name: str, last_seq: int = 0) -> None:
        self.name = name
        self.last_seq = last_seq


class ProjectorRegistry:
    """Hold zero-or-more projectors and their replay cursors.

    All projectors are advanced together in :meth:`replay`; per-projector
    cursors are exposed via :meth:`cursor_for` so a host can persist them
    out-of-band (e.g. in a separate SQLite table) and restore them on
    restart.

    The registry is *transactional* per event: if any projector raises,
    every projector's cursor is rolled back to the last successfully
    processed seq before the exception propagates.
    """

    def __init__(self) -> None:
        self._projectors: dict[str, Projector] = {}
        self._states: dict[str, ProjectorState] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, projector: Projector) -> None:
        """Register *projector*.

        Raises:
            ValueError: if a projector with the same ``name`` is already
                registered or the projector has an empty name.
        """
        if not projector.name:
            raise ValueError("Projector.name must be non-empty")
        if projector.name in self._projectors:
            raise ValueError(f"Projector {projector.name!r} already registered")
        self._projectors[projector.name] = projector
        self._states[projector.name] = ProjectorState(projector.name, last_seq=0)

    def projectors(self) -> list[Projector]:
        """Return registered projectors (registration order)."""
        return list(self._projectors.values())

    def cursor_for(self, name: str) -> int:
        """Return the last successfully processed seq for projector *name*."""
        return self._states[name].last_seq

    def set_cursor(self, name: str, seq: int) -> None:
        """Manually set a projector's cursor (used after restart)."""
        self._states[name].last_seq = seq

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def replay(
        self,
        events: Iterable[tuple[int, str, Any]],
    ) -> int:
        """Apply each event to every registered projector.

        Args:
            events: Iterable of ``(seq, event_name, payload)`` triples in
                ascending ``seq`` order.

        Returns:
            The number of events successfully applied.

        Raises:
            ProjectorError: if any projector raises; cursors of *all*
                projectors are rolled back to their state before the
                failing event, so re-running :meth:`replay` from the
                store will retry the same seq.
        """
        applied = 0
        for seq, event_name, payload in events:
            # Snapshot cursors so we can roll back on failure.
            snapshot = {n: s.last_seq for n, s in self._states.items()}
            try:
                for projector in self._projectors.values():
                    state = self._states[projector.name]
                    if seq <= state.last_seq:
                        # Idempotent skip — projector has already seen this seq.
                        continue
                    projector.apply(event_name, payload)
                    state.last_seq = seq
                applied += 1
            except Exception as exc:
                # Roll back every projector to the pre-event snapshot.
                for n, last_seq in snapshot.items():
                    self._states[n].last_seq = last_seq
                # Find the projector that failed for the error message.
                culprit = "unknown"
                for projector in self._projectors.values():
                    state = self._states[projector.name]
                    if state.last_seq == snapshot[projector.name]:
                        culprit = projector.name
                        break
                raise ProjectorError(culprit, seq, exc) from exc
        return applied

    def reset(self) -> None:
        """Reset every projector's state and rewind cursors to zero."""
        for projector in self._projectors.values():
            projector.reset()
        for state in self._states.values():
            state.last_seq = 0

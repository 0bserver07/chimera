"""FeedbackTracker: auto-tracks error->fix outcomes via EventBus."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.events.base import Event

from chimera.learning.observation import CATEGORY_THRESHOLDS, Observation, ObservationCategory
from chimera.learning.store import LearningStore

__all__ = ["FeedbackTracker"]


def _normalize_error(text: str) -> str:
    """Normalize an error message for signature generation.

    Strips line numbers, hex addresses, timestamps, and extra whitespace
    to produce a stable signature across runs.
    """
    # Remove hex addresses like 0x7fff5fbff8c0
    text = re.sub(r"0x[0-9a-fA-F]+", "<addr>", text)
    # Remove line numbers like :42: or line 42
    text = re.sub(r":\d+:", ":<line>:", text)
    text = re.sub(r"line \d+", "line <N>", text)
    # Remove timestamps
    text = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "<time>", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _error_signature(text: str) -> str:
    """Compute MD5 signature of a normalized error message."""
    normalized = _normalize_error(text)
    return hashlib.md5(normalized.encode()).hexdigest()


@dataclass
class _PendingFeedback:
    """Tracks a pending feedback window for an error observation."""

    observation_id: int | None
    error_signature: str
    remaining: int
    seen_again: bool = False


class FeedbackTracker:
    """Auto-tracks error->fix outcomes via EventBus.

    After a tool error: opens a feedback window (next ``window_size`` tool calls).
    If the error disappears within the window, confidence is updated as success.
    If the error reappears, confidence is updated as failure.

    Args:
        store: The learning store for recording and querying observations.
        window_size: Number of subsequent tool results to monitor.
    """

    def __init__(self, store: LearningStore, window_size: int = 3) -> None:
        self._store = store
        self._window_size = window_size
        self._pending: list[_PendingFeedback] = []

    def on_tool_result(self, event: Event) -> None:
        """Handle a ToolResultEvent.

        This is the main entry point, called by EventBus subscription.

        Args:
            event: A ToolResultEvent with ``success`` and ``output`` fields.
        """
        # Import here to avoid circular imports at module level
        from chimera.events.types import ToolResultEvent

        if not isinstance(event, ToolResultEvent):
            return

        is_error = not event.success
        signature = _error_signature(event.output) if event.output else ""

        # Update pending feedback windows
        self._update_pending(signature, is_error)

        # If this is an error, start tracking
        if is_error and signature:
            self._handle_error(event.output, signature)

    def _update_pending(self, current_signature: str, is_error: bool) -> None:
        """Update all pending feedback windows with the current result."""
        completed: list[_PendingFeedback] = []

        for pending in self._pending:
            pending.remaining -= 1

            if is_error and current_signature == pending.error_signature:
                pending.seen_again = True

            if pending.remaining <= 0:
                completed.append(pending)

        # Process completed windows
        for pending in completed:
            self._pending.remove(pending)
            if pending.observation_id is not None:
                if pending.seen_again:
                    self._store.update_confidence(pending.observation_id, success=False)
                else:
                    self._store.update_confidence(pending.observation_id, success=True)

    def _handle_error(self, error_text: str, signature: str) -> None:
        """Handle a new error: look up or record, then open feedback window."""
        existing = self._store.query_by_signature(signature)

        if existing is not None:
            # Check if above threshold — we have a known fix
            threshold = CATEGORY_THRESHOLDS.get(existing.category, 0.5)
            obs_id = existing.id
            if existing.confidence >= threshold:
                # Known fix with sufficient confidence — track outcome
                self._pending.append(
                    _PendingFeedback(
                        observation_id=obs_id,
                        error_signature=signature,
                        remaining=self._window_size,
                    )
                )
            else:
                # Known but low confidence — still track
                self._pending.append(
                    _PendingFeedback(
                        observation_id=obs_id,
                        error_signature=signature,
                        remaining=self._window_size,
                    )
                )
        else:
            # New observation — record it
            observation = Observation(
                topic="error",
                key=signature,
                value=error_text,
                category=ObservationCategory.ERROR,
                confidence=0.5,
                error_signature=signature,
            )
            self._store.record(observation)
            # Look it up to get the ID
            recorded = self._store.query_by_signature(signature)
            obs_id = recorded.id if recorded else None
            self._pending.append(
                _PendingFeedback(
                    observation_id=obs_id,
                    error_signature=signature,
                    remaining=self._window_size,
                )
            )

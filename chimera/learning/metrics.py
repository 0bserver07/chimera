"""SessionMetrics and MetricsCollector for adaptive learning."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chimera.events.base import Event
    from chimera.learning.store import LearningStore

__all__ = ["MetricsCollector", "SessionMetrics"]


@dataclass
class SessionMetrics:
    """Aggregated metrics for a single agent session.

    Args:
        session_id: Unique identifier for this session.
        start_time: ISO 8601 timestamp when the session began.
        tool_calls: Total tool invocations.
        files_modified: Number of unique files modified.
        errors_encountered: Total errors seen.
        errors_resolved: Errors that were subsequently resolved.
        observations_recorded: New observations added to the store.
        total_cost: Accumulated API cost for this session.
    """

    session_id: str
    start_time: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    tool_calls: int = 0
    files_modified: int = 0
    errors_encountered: int = 0
    errors_resolved: int = 0
    observations_recorded: int = 0
    total_cost: float = 0.0


class MetricsCollector:
    """Subscribe to EventBus and aggregate session metrics.

    Args:
        store: Optional learning store for recording observations.
    """

    def __init__(self, store: LearningStore | None = None) -> None:
        self._store = store
        self._metrics = SessionMetrics(session_id="")
        self._modified_files: set[str] = set()
        self._error_signatures: set[str] = set()
        self._resolved_signatures: set[str] = set()

    @property
    def metrics(self) -> SessionMetrics:
        """Current session metrics snapshot."""
        return self._metrics

    def start_session(self, session_id: str) -> None:
        """Initialize metrics for a new session.

        Args:
            session_id: Unique session identifier.
        """
        self._metrics = SessionMetrics(session_id=session_id)
        self._modified_files.clear()
        self._error_signatures.clear()
        self._resolved_signatures.clear()

    def on_tool_call(self, event: Event) -> None:
        """Handle a ToolCallEvent.

        Args:
            event: A ToolCallEvent.
        """
        self._metrics.tool_calls += 1

        # Track file modifications
        from chimera.events.types import ToolCallEvent

        if isinstance(event, ToolCallEvent):
            tool_name = event.tool_name
            if tool_name in ("write", "edit", "replace_in_file"):
                path = event.arguments.get("file_path", "") or event.arguments.get("path", "")
                if path and path not in self._modified_files:
                    self._modified_files.add(path)
                    self._metrics.files_modified = len(self._modified_files)

    def on_tool_result(self, event: Event) -> None:
        """Handle a ToolResultEvent.

        Args:
            event: A ToolResultEvent.
        """
        from chimera.events.types import ToolResultEvent

        if not isinstance(event, ToolResultEvent):
            return

        if not event.success:
            self._metrics.errors_encountered += 1

    def on_step_cost(self, event: Event) -> None:
        """Handle a StepCostEvent.

        Args:
            event: A StepCostEvent.
        """
        from chimera.events.types import StepCostEvent

        if isinstance(event, StepCostEvent):
            self._metrics.total_cost += event.cost

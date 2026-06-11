"""ATIFReader — load ATIF trajectories and convert them to Chimera events.

Consumes trajectories produced by any ATIF emitter (Chimera's own, or
Pier-generated DeepSWE runs) for cross-framework analysis.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.atif.validator import ATIFValidator, ValidationResult
from chimera.events.base import Event
from chimera.events.types import (
    CompactionEvent,
    StepEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class ATIFReader:
    """Parse ATIF v1.7 trajectory files."""

    def load(self, path: str | Path, validate: bool = True) -> dict[str, Any]:
        """Load a trajectory document from disk.

        Args:
            path: The trajectory JSON file.
            validate: When ``True`` (default), raise on invalid documents.

        Returns:
            The trajectory as a dict.

        Raises:
            ValueError: If the file is not valid JSON, or fails validation
                when ``validate`` is set.
        """
        raw = Path(path).read_text(encoding="utf-8")
        try:
            trajectory: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON: {exc}") from exc
        if validate:
            result = self.validate_dict(trajectory)
            if not result.valid:
                raise ValueError(
                    f"{path}: invalid ATIF trajectory: " + "; ".join(result.errors)
                )
        return trajectory

    def validate(self, path: str | Path) -> ValidationResult:
        """Validate a trajectory file without raising.

        Args:
            path: The trajectory JSON file.

        Returns:
            The :class:`~chimera.atif.validator.ValidationResult`.
        """
        try:
            trajectory = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result = ValidationResult()
            result.add(f"{path}: unreadable: {exc}")
            return result
        return self.validate_dict(trajectory)

    def validate_dict(self, trajectory: dict[str, Any]) -> ValidationResult:
        """Validate an in-memory trajectory dict."""
        return ATIFValidator().check(trajectory)

    def to_events(self, trajectory: dict[str, Any]) -> list[Event]:
        """Convert trajectory steps into Chimera event objects.

        Agent text becomes :class:`StepEvent`, tool calls/observations
        become :class:`ToolCallEvent` / :class:`ToolResultEvent`, and
        recorded summarizations become :class:`CompactionEvent`. Lossy by
        nature (ATIF carries more than the event surface), but sufficient
        for Chimera's analyzers.

        Args:
            trajectory: A trajectory dict (see :meth:`load`).

        Returns:
            Events in step order.
        """
        events: list[Event] = []
        agent_step_no = 0
        for step in trajectory.get("steps", []):
            if step.get("source") != "agent":
                continue
            agent_step_no += 1
            message = step.get("message")
            events.append(
                StepEvent(
                    step_number=agent_step_no,
                    content=message if isinstance(message, str) else "",
                )
            )
            for tc in step.get("tool_calls") or []:
                events.append(
                    ToolCallEvent(
                        tool_name=tc.get("function_name", ""),
                        arguments=dict(tc.get("arguments") or {}),
                        call_id=tc.get("tool_call_id", ""),
                    )
                )
            observation = step.get("observation") or {}
            for res in observation.get("results") or []:
                content = res.get("content")
                events.append(
                    ToolResultEvent(
                        call_id=res.get("source_call_id", ""),
                        output=content if isinstance(content, str) else "",
                        success=(res.get("extra") or {}).get("success", True),
                    )
                )

        extra = (trajectory.get("final_metrics") or {}).get("extra") or {}
        for _ in range(int(extra.get("summarization_count") or 0)):
            events.append(CompactionEvent())
        return events

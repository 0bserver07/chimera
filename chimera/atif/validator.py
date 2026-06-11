"""ATIFValidator — shape and structural checks for ATIF v1.7 trajectories.

Mirrors the upstream Pier model validators (required fields, source
enum, agent-only fields, the v1.7 ``llm_call_count == 0`` rule, ISO 8601
timestamps) plus the structural rules from
``docs/specs/atif-trajectory-emission.md`` (ordinal step ids, monotonic
non-decreasing timestamps). Stdlib-only by design — full JSON-Schema
validation against ``chimera/atif/schema.json`` is intentionally not
re-implemented here; these checks cover every rule the upstream models
enforce.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_SCHEMA_VERSIONS = {f"ATIF-v1.{i}" for i in range(8)}
_SOURCES = {"system", "user", "agent"}
_AGENT_ONLY_FIELDS = (
    "model_name",
    "reasoning_effort",
    "reasoning_content",
    "tool_calls",
    "metrics",
)


@dataclass
class ValidationResult:
    """Outcome of a trajectory validation.

    Attributes:
        valid: ``True`` when no errors were found.
        errors: Human-readable problems, each prefixed with its location.
    """

    valid: bool = True
    errors: list[str] = field(default_factory=list)

    def add(self, error: str) -> None:
        self.valid = False
        self.errors.append(error)


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ATIFValidator:
    """Validate a trajectory dict against ATIF v1.7 rules."""

    def check(self, trajectory: dict[str, Any]) -> ValidationResult:
        """Run all checks.

        Args:
            trajectory: A parsed trajectory document.

        Returns:
            A :class:`ValidationResult`; ``errors`` lists every violation
            found (the check does not stop at the first).
        """
        result = ValidationResult()
        if not isinstance(trajectory, dict):
            result.add("trajectory: not a JSON object")
            return result

        version = trajectory.get("schema_version", "ATIF-v1.7")
        if version not in _SCHEMA_VERSIONS:
            result.add(f"schema_version: unknown value {version!r}")

        agent = trajectory.get("agent")
        if not isinstance(agent, dict):
            result.add("agent: required object missing")
        else:
            for key in ("name", "version"):
                if not agent.get(key):
                    result.add(f"agent.{key}: required field missing")

        steps = trajectory.get("steps")
        if not isinstance(steps, list) or not steps:
            result.add("steps: required non-empty array missing")
            return result

        last_ts: datetime | None = None
        for i, step in enumerate(steps):
            loc = f"steps[{i}]"
            if not isinstance(step, dict):
                result.add(f"{loc}: not an object")
                continue

            step_id = step.get("step_id")
            if step_id != i + 1:
                result.add(f"{loc}.step_id: expected ordinal {i + 1}, got {step_id!r}")

            source = step.get("source")
            if source not in _SOURCES:
                result.add(f"{loc}.source: must be one of {sorted(_SOURCES)}, got {source!r}")

            if "message" not in step:
                result.add(f"{loc}.message: required field missing")

            if source != "agent":
                for fname in _AGENT_ONLY_FIELDS:
                    if step.get(fname) is not None:
                        result.add(
                            f"{loc}.{fname}: only applicable when source is 'agent' "
                            f"(source is {source!r})"
                        )

            if step.get("llm_call_count") == 0 and source == "agent":
                for fname in ("metrics", "reasoning_content"):
                    if step.get(fname) is not None:
                        result.add(
                            f"{loc}.{fname}: must be absent when llm_call_count is 0 "
                            "(deterministic dispatch)"
                        )

            for j, tc in enumerate(step.get("tool_calls") or []):
                tloc = f"{loc}.tool_calls[{j}]"
                if not isinstance(tc, dict):
                    result.add(f"{tloc}: not an object")
                    continue
                for key in ("tool_call_id", "function_name", "arguments"):
                    if key not in tc:
                        result.add(f"{tloc}.{key}: required field missing")

            observation = step.get("observation")
            if observation is not None and (
                not isinstance(observation, dict) or "results" not in observation
            ):
                result.add(f"{loc}.observation.results: required field missing")

            ts_raw = step.get("timestamp")
            if ts_raw is not None:
                ts = _parse_timestamp(str(ts_raw))
                if ts is None:
                    result.add(f"{loc}.timestamp: invalid ISO 8601 value {ts_raw!r}")
                else:
                    if last_ts is not None and ts < last_ts:
                        result.add(
                            f"{loc}.timestamp: not monotonically non-decreasing "
                            f"({ts.isoformat()} < {last_ts.isoformat()})"
                        )
                    last_ts = ts

        return result

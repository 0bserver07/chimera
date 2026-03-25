"""Observation data model and category thresholds for adaptive learning."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = ["CATEGORY_THRESHOLDS", "Observation", "ObservationCategory"]


class ObservationCategory(Enum):
    """Categories of learned observations."""

    ERROR = "error"
    DEBUG = "debug"
    DESIGN = "design"
    REVIEW = "review"
    EFFECTIVENESS = "effectiveness"


CATEGORY_THRESHOLDS: dict[ObservationCategory, float] = {
    ObservationCategory.ERROR: 0.50,
    ObservationCategory.DEBUG: 0.60,
    ObservationCategory.DESIGN: 0.70,
    ObservationCategory.REVIEW: 0.70,
    ObservationCategory.EFFECTIVENESS: 0.50,
}


@dataclass
class Observation:
    """A single learned observation from agent operation.

    Args:
        topic: High-level topic (e.g. "import_error", "test_failure").
        key: Specific key within the topic.
        value: The learned fix / pattern / insight.
        category: Classification of this observation.
        confidence: Current confidence score in [0.0, 1.0].
        tags: Free-form tags for filtering.
        source: Where this observation originated.
        project_path: Scoped to a specific project directory.
        error_signature: MD5 of normalized error message for dedup.
        observation_count: Number of times this was observed.
        success_count: Number of successful fix applications.
        failure_count: Number of failed fix applications.
    """

    topic: str
    key: str
    value: str
    category: ObservationCategory
    confidence: float = 0.5
    tags: list[str] = field(default_factory=list)
    source: str = ""
    project_path: str = ""
    error_signature: str = ""
    observation_count: int = 1
    success_count: int = 0
    failure_count: int = 0
    id: int | None = None

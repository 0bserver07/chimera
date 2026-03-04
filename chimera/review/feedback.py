"""Structured code review feedback."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    INFO = "info"
    SUGGESTION = "suggestion"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ReviewComment:
    """A single review comment."""
    file: str
    line: int = 0
    severity: Severity = Severity.SUGGESTION
    message: str = ""
    suggestion: str = ""  # suggested fix

    @property
    def summary(self) -> str:
        loc = self.file
        if self.line:
            loc += f":{self.line}"
        return f"[{self.severity.value.upper()}] {loc}: {self.message}"


@dataclass
class ReviewFeedback:
    """Aggregated review feedback."""
    comments: list[ReviewComment] = field(default_factory=list)
    approved: bool = False
    summary: str = ""

    @property
    def has_critical(self) -> bool:
        return any(c.severity == Severity.CRITICAL for c in self.comments)

    @property
    def has_errors(self) -> bool:
        return any(c.severity in (Severity.ERROR, Severity.CRITICAL) for c in self.comments)

    @property
    def comment_count(self) -> int:
        return len(self.comments)

    def by_severity(self, severity: Severity) -> list[ReviewComment]:
        return [c for c in self.comments if c.severity == severity]

    def by_file(self, file: str) -> list[ReviewComment]:
        return [c for c in self.comments if c.file == file]

    @property
    def files_reviewed(self) -> list[str]:
        return sorted(set(c.file for c in self.comments))

    @staticmethod
    def parse_from_text(text: str) -> ReviewFeedback:
        """Parse review feedback from agent text output.

        Expected format per comment:
            [SEVERITY] file:line: message
        """
        feedback = ReviewFeedback()
        severity_map = {s.value.upper(): s for s in Severity}

        for match in re.finditer(
            r"\[(\w+)\]\s+([\w/.-]+)(?::(\d+))?:\s*(.+)",
            text,
        ):
            sev_str = match.group(1).upper()
            severity = severity_map.get(sev_str, Severity.SUGGESTION)
            feedback.comments.append(ReviewComment(
                file=match.group(2),
                line=int(match.group(3)) if match.group(3) else 0,
                severity=severity,
                message=match.group(4).strip(),
            ))

        # Check for approval
        if re.search(r"\bapproved?\b", text, re.IGNORECASE) and not feedback.has_errors:
            feedback.approved = True

        return feedback

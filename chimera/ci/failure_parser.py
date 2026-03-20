"""Parse CI failure logs to extract actionable failure information."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class FailureInfo:
    """Extracted information about a CI failure."""
    test_name: str = ""
    file_path: str = ""
    line_number: int = 0
    error_type: str = ""
    error_message: str = ""
    stack_trace: str = ""

    @property
    def summary(self) -> str:
        parts = []
        if self.file_path:
            loc = self.file_path
            if self.line_number:
                loc += f":{self.line_number}"
            parts.append(loc)
        if self.test_name:
            parts.append(self.test_name)
        if self.error_type:
            parts.append(self.error_type)
        if self.error_message:
            parts.append(self.error_message[:100])
        return " | ".join(parts) if parts else "Unknown failure"


def parse_ci_log(log: str) -> list[FailureInfo]:
    """Parse a CI log and extract failure information.

    Handles pytest, jest, go test, and cargo test output formats.
    """
    failures: list[FailureInfo] = []

    # Pytest failures: FAILED tests/test_foo.py::test_bar - ErrorType: message
    for match in re.finditer(
        r"FAILED\s+([\w/.-]+)::(\w+)(?:\s*-\s*(\w+):\s*(.+))?",
        log,
    ):
        failures.append(FailureInfo(
            file_path=match.group(1),
            test_name=match.group(2),
            error_type=match.group(3) or "",
            error_message=match.group(4) or "",
        ))

    # Pytest traceback: file.py:123: ErrorType
    for match in re.finditer(
        r"([\w/.-]+\.py):(\d+):\s+(\w+Error\w*)",
        log,
    ):
        # Only add if not already captured
        path = match.group(1)
        if not any(f.file_path == path and f.line_number == int(match.group(2)) for f in failures):
            failures.append(FailureInfo(
                file_path=path,
                line_number=int(match.group(2)),
                error_type=match.group(3),
            ))

    # Jest failures: FAIL src/foo.test.ts
    for match in re.finditer(r"FAIL\s+([\w/.-]+)", log):
        path = match.group(1)
        if not any(f.file_path == path for f in failures):
            failures.append(FailureInfo(file_path=path))

    # Go test: --- FAIL: TestFoo (0.00s)
    for match in re.finditer(r"---\s*FAIL:\s+(\w+)\s+\(", log):
        failures.append(FailureInfo(test_name=match.group(1)))

    # Cargo test: test module::test_name ... FAILED
    for match in re.finditer(r"test\s+([\w:]+)\s+\.\.\.\s+FAILED", log):
        failures.append(FailureInfo(test_name=match.group(1)))

    # Generic error extraction if no specific failures found
    if not failures:
        for match in re.finditer(r"(?:Error|Exception|FATAL|CRITICAL):\s*(.+)", log, re.IGNORECASE):
            failures.append(FailureInfo(error_message=match.group(1).strip()))

    return failures

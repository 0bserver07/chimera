"""Coverage report parsing."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class CoverageReport:
    """Parsed coverage report."""
    total_statements: int = 0
    total_missing: int = 0
    coverage_percent: float = 0.0
    file_coverage: dict[str, float] = field(default_factory=dict)
    uncovered_lines: dict[str, list[int]] = field(default_factory=dict)

    @property
    def covered_statements(self) -> int:
        return self.total_statements - self.total_missing

    def files_below(self, threshold: float) -> list[str]:
        """Return files with coverage below threshold."""
        return [f for f, pct in self.file_coverage.items() if pct < threshold]


def parse_coverage(output: str) -> CoverageReport:
    """Parse pytest-cov or coverage.py output.

    Expected format:
        Name               Stmts   Miss  Cover   Missing
        -----------------------------------------------
        src/foo.py            50     10    80%   12-15, 30
        src/bar.py            30      0   100%
        -----------------------------------------------
        TOTAL                 80     10    88%
    """
    report = CoverageReport()

    for match in re.finditer(
        r"^([\w/.-]+\.py)\s+(\d+)\s+(\d+)\s+(\d+)%(?:\s+(.+))?$",
        output,
        re.MULTILINE,
    ):
        filepath = match.group(1)
        stmts = int(match.group(2))
        miss = int(match.group(3))
        cover = float(match.group(4))
        missing = match.group(5) or ""

        report.file_coverage[filepath] = cover

        # Parse missing line ranges
        if missing.strip():
            lines: list[int] = []
            for part in missing.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-", 1)
                    try:
                        lines.extend(range(int(start), int(end) + 1))
                    except ValueError:
                        pass
                else:
                    try:
                        lines.append(int(part))
                    except ValueError:
                        pass
            if lines:
                report.uncovered_lines[filepath] = lines

    # Parse TOTAL line
    total_match = re.search(r"TOTAL\s+(\d+)\s+(\d+)\s+(\d+)%", output)
    if total_match:
        report.total_statements = int(total_match.group(1))
        report.total_missing = int(total_match.group(2))
        report.coverage_percent = float(total_match.group(3))

    return report

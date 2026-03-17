"""Fault localization via test failure analysis.

Ranks code locations by suspiciousness using an Ochiai-inspired approach:
parse test output, extract traceback references, and score locations
that appear more frequently in failing tests higher.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SuspiciousLocation:
    """A code location ranked by suspiciousness."""

    file: str
    function: str | None
    line: int
    score: float  # 0.0 = not suspicious, 1.0 = very suspicious
    reason: str


class FaultLocalizer:
    """Rank code locations by suspiciousness using test failure analysis.

    Uses a simplified Ochiai-style approach:
    - Parse test output to identify which tests failed and their tracebacks
    - Extract file:line references from tracebacks
    - Score locations that appear more in failing tests higher
    """

    def localize(self, test_output: str) -> list[SuspiciousLocation]:
        """Parse test output and rank suspicious locations.

        Args:
            test_output: Raw pytest output containing failures and tracebacks.

        Returns:
            Locations sorted by suspiciousness (highest first).
        """
        # Extract file:line references from tracebacks
        # Pattern: "path/to/file.py:42: AssertionError" or similar
        location_pattern = re.compile(r"(\S+\.py):(\d+)")

        # Count how many distinct failure blocks reference each location
        failure_blocks = test_output.split("FAILED")

        location_counts: dict[tuple[str, int], int] = {}
        total_failures = 0

        for block in failure_blocks[1:]:  # skip everything before first FAILED
            total_failures += 1
            seen_in_block: set[tuple[str, int]] = set()
            for match in location_pattern.finditer(block):
                filepath = match.group(1)
                line = int(match.group(2))
                # Skip test files
                if "test_" in filepath or filepath.startswith("test"):
                    continue
                loc = (filepath, line)
                seen_in_block.add(loc)
            for loc in seen_in_block:
                location_counts[loc] = location_counts.get(loc, 0) + 1

        if not location_counts or total_failures == 0:
            return []

        # Ochiai-inspired scoring: score = count / sqrt(total_failures * count)
        # Simplified: score = count / total_failures (frequency-based)
        results = []
        for (filepath, line), count in location_counts.items():
            score = count / total_failures
            results.append(
                SuspiciousLocation(
                    file=filepath,
                    function=None,  # Could enhance with AST to find enclosing function
                    line=line,
                    score=round(score, 3),
                    reason=f"Referenced in {count}/{total_failures} failing tests",
                )
            )

        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def augment_prompt(
        self,
        prompt: str,
        locations: list[SuspiciousLocation],
        max_locations: int = 3,
    ) -> str:
        """Add fault localization info to an agent prompt.

        Args:
            prompt: The original agent prompt.
            locations: Ranked suspicious locations from ``localize()``.
            max_locations: Maximum number of locations to include.

        Returns:
            The prompt with suspected bug locations appended.
        """
        if not locations:
            return prompt

        lines = ["\n\n## Suspected Bug Locations\n"]
        lines.append("Based on test failure analysis, the bug is most likely in:\n")
        for i, loc in enumerate(locations[:max_locations], 1):
            func = f" ({loc.function})" if loc.function else ""
            lines.append(
                f"{i}. `{loc.file}:{loc.line}`{func}"
                f" — suspiciousness {loc.score:.0%}"
            )
        lines.append("\nFocus your fix on these locations first.")

        return prompt + "\n".join(lines)

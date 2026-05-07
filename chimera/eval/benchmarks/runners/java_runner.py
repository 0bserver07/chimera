"""Java language runner for MultiSWE-bench (Maven)."""
from __future__ import annotations

from chimera.eval.benchmarks.runners.base import LanguageRunner

JAVA_RUNNER = LanguageRunner(
    language="java",
    test_command="mvn -q -B test",
    toolchain_command="mvn --version",
    display_name="Java (Maven)",
)

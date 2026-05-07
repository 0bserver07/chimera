"""JavaScript / TypeScript language runner for MultiSWE-bench (npm test)."""
from __future__ import annotations

from chimera.eval.benchmarks.runners.base import LanguageRunner

JAVASCRIPT_RUNNER = LanguageRunner(
    language="javascript",
    test_command="npm test --silent",
    toolchain_command="node --version",
    display_name="JavaScript / TypeScript (npm test)",
)

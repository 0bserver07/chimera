"""Go language runner for MultiSWE-bench (``go test``)."""
from __future__ import annotations

from chimera.eval.benchmarks.runners.base import LanguageRunner

GO_RUNNER = LanguageRunner(
    language="go",
    test_command="go test ./...",
    toolchain_command="go version",
    display_name="Go (go test)",
)

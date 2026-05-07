"""Python language runner for MultiSWE-bench (pytest)."""
from __future__ import annotations

from chimera.eval.benchmarks.runners.base import LanguageRunner

PYTHON_RUNNER = LanguageRunner(
    language="python",
    test_command="pytest -x --no-header -rN",
    toolchain_command="python --version",
    display_name="Python (pytest)",
)

"""Rust language runner for MultiSWE-bench (``cargo test``)."""
from __future__ import annotations

from chimera.eval.benchmarks.runners.base import LanguageRunner

RUST_RUNNER = LanguageRunner(
    language="rust",
    test_command="cargo test --quiet",
    toolchain_command="cargo --version",
    display_name="Rust (cargo test)",
)

"""Language-specific test runners for MultiSWE-bench.

Each runner encapsulates the test command, toolchain probe, and patch-application
behavior for one language. ``MultiSWEBench.evaluate`` looks up the runner for an
instance's ``language`` field and dispatches to it.

Adding a new language: define a new ``LanguageRunner`` with its
``test_command`` (e.g. ``"go test ./..."``) and ``toolchain_command``
(e.g. ``"go version"``), then register it in :data:`RUNNERS`.

The runners deliberately avoid spawning real subprocesses themselves —
they call ``env.run_command`` so that mocked / Docker / remote environments
all work uniformly.
"""
from __future__ import annotations

from chimera.eval.benchmarks.runners.base import (
    LanguageRunner,
    RunnerResult,
    SkipReason,
)
from chimera.eval.benchmarks.runners.go_runner import GO_RUNNER
from chimera.eval.benchmarks.runners.java_runner import JAVA_RUNNER
from chimera.eval.benchmarks.runners.javascript_runner import JAVASCRIPT_RUNNER
from chimera.eval.benchmarks.runners.python_runner import PYTHON_RUNNER
from chimera.eval.benchmarks.runners.rust_runner import RUST_RUNNER

#: Registry of supported language runners keyed by lowercase language name.
RUNNERS: dict[str, LanguageRunner] = {
    "python": PYTHON_RUNNER,
    "java": JAVA_RUNNER,
    "go": GO_RUNNER,
    "javascript": JAVASCRIPT_RUNNER,
    "js": JAVASCRIPT_RUNNER,
    "typescript": JAVASCRIPT_RUNNER,
    "ts": JAVASCRIPT_RUNNER,
    "rust": RUST_RUNNER,
}


def get_runner(language: str) -> LanguageRunner | None:
    """Return the runner for ``language`` (case-insensitive), or ``None``.

    Args:
        language: Language identifier such as ``"Python"`` or ``"go"``.

    Returns:
        The matching :class:`LanguageRunner` or ``None`` if unsupported.
    """
    if not language:
        return None
    return RUNNERS.get(language.lower())


__all__ = [
    "GO_RUNNER",
    "JAVASCRIPT_RUNNER",
    "JAVA_RUNNER",
    "LanguageRunner",
    "PYTHON_RUNNER",
    "RUNNERS",
    "RUST_RUNNER",
    "RunnerResult",
    "SkipReason",
    "get_runner",
]

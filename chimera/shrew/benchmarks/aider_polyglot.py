"""Shrew-flavoured Aider Polyglot adapter.

Thin wrapper around :class:`chimera.eval.benchmarks.aider_polyglot.AiderPolyglot`
— the general adapter shared by every Chimera CLI. The shrew flavour
exists so the small-model harness can carry tighter defaults without
forking the dataset-loading + scoring logic:

* Default language subset trimmed to a small-model-friendly slice
  (``python``, ``javascript``, ``rust``, ``go``) — Java and C++ in the
  general adapter need toolchains that aren't on every shrew laptop.
* Tighter per-task subprocess timeout via the ``timeout_s`` task field
  is left to the dataset author; we don't override the upstream default
  here so a shared dataset still grades identically.

For the dataset schema, env-var override, and grading semantics see the
docstring on the general adapter.
"""
from __future__ import annotations

from chimera.eval.benchmarks.aider_polyglot import (
    DEFAULT_DATASET_DIR,
    ENV_DATASET_PATH,
    SUPPORTED_LANGUAGES,
    AiderPolyglot as _GeneralAiderPolyglot,
    dataset_available,
    default_dataset_path,
    setup_hint,
)

__all__ = [
    "AiderPolyglot",
    "DEFAULT_DATASET_DIR",
    "ENV_DATASET_PATH",
    "SHREW_DEFAULT_LANGUAGES",
    "SUPPORTED_LANGUAGES",
    "default_dataset_path",
    "dataset_available",
    "setup_hint",
]


SHREW_DEFAULT_LANGUAGES: tuple[str, ...] = (
    "python",
    "javascript",
    "rust",
    "go",
)
"""Languages the shrew flavour exposes by default.

Java and C++ are dropped because their test harnesses (``mvn``, ``cmake``)
aren't always installed on a small-model laptop. Callers can opt back in
by passing ``languages=list(SUPPORTED_LANGUAGES)`` explicitly.
"""


class AiderPolyglot(_GeneralAiderPolyglot):
    """Shrew-flavoured Aider Polyglot adapter.

    Inherits the full dataset-loading + scoring pipeline from the
    general adapter; only the construction defaults change.

    Args:
        dataset_path: Optional path to a directory containing
            ``tasks.json``. When ``None``, the env-var / home-dir
            default is used.
        limit: Optional cap on the number of tasks returned.
        languages: Optional list of language filters. When ``None`` the
            shrew default subset is left *unfiltered* so the CLI's
            existing ``--language`` flag remains the primary control
            surface; callers who want the shrew narrow set explicitly
            can pass ``languages=list(SHREW_DEFAULT_LANGUAGES)``.
        language: Back-compat single-language filter (forwarded to the
            general adapter).
    """

    # No method overrides — the general implementation is correct as-is.
    # The class exists so ``isinstance(b, AiderPolyglot)`` checks in
    # shrew-specific call sites keep distinguishing flavours, and so
    # future shrew-only knobs (tighter step budget, narrower prompt
    # template) have a clear home.

"""Friendly user-facing error wrappers for Chimera CLIs.

This package converts low-level provider / network exceptions (raw stack
traces from ``anthropic``, ``openai``, ``httpx`` SDKs and provider-factory
``ValueError``\\s) into actionable :class:`ChimeraUserError` instances with
remediation hints. Each Chimera CLI's ``run(args)`` is wrapped with the
:func:`friendly_errors` decorator so end users see a colored one-line
message + dim hint instead of an opaque traceback — unless ``--debug``
is set, in which case the original exception is re-raised verbatim.

Public API:

* :class:`ChimeraUserError` — the wrapper exception (message + hint +
  category + exit code).
* :func:`wrap_provider_errors` — context manager that performs the
  raw-exception → :class:`ChimeraUserError` mapping.
* :func:`friendly_errors` — decorator for CLI ``run(args)`` entry points
  that prints the friendly message and returns ``exit_code`` (or
  re-raises when ``args.debug`` is true).

All implementation lives in :mod:`chimera.errors.friendly`; this module
re-exports the surface so callers can ``from chimera.errors import …``.
"""

from chimera.errors.friendly import (
    ChimeraUserError,
    friendly_errors,
    wrap_provider_errors,
)

__all__ = [
    "ChimeraUserError",
    "friendly_errors",
    "wrap_provider_errors",
]

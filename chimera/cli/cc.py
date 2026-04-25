"""Deprecated alias for :mod:`chimera.mink.cli`.

The module was renamed to ``chimera.mink.cli``. Importers should switch
to that module (or the convenience re-exports on ``chimera.mink``);
this shim re-exports the public surface and emits a
:class:`DeprecationWarning` on import. Kept only so that legacy ``cc_*``
import paths continue to work for one release cycle.
"""
from __future__ import annotations

import warnings

from chimera.mink.cli import add_arguments, run

warnings.warn(
    "chimera.cli.cc is deprecated; import from chimera.mink (or "
    "chimera.mink.cli) instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["add_arguments", "run"]

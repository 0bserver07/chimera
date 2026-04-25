"""Deprecated alias for :mod:`chimera.mink.team`.

The module was renamed to ``chimera.mink.team``. Importers should switch
to that module; this shim re-exports the public surface and emits a
:class:`DeprecationWarning` on import. Kept only so that legacy ``cc_*``
import paths continue to work for one release cycle.
"""
from __future__ import annotations

import warnings

from chimera.mink.team import main, register, run

warnings.warn(
    "chimera.cli.cc_team_subcommand is deprecated; "
    "import from chimera.mink.team instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["main", "register", "run"]

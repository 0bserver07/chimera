"""Deprecated alias for :mod:`chimera.mink.settings`.

The module was renamed to ``chimera.mink.settings``. Importers should
switch to that module; this shim re-exports the public surface (under
both the old ``CCSettings`` / ``CCSettingsError`` / ``load_cc_settings``
names AND the new ``Mink*`` names) and emits a
:class:`DeprecationWarning` on import. Kept only so that legacy ``cc_*``
import paths continue to work for one release cycle.
"""
from __future__ import annotations

import warnings

from chimera.mink.settings import (
    MinkSettings,
    MinkSettings as CCSettings,
    MinkSettingsError,
    MinkSettingsError as CCSettingsError,
    Permissions,
    load_mink_settings,
    load_mink_settings as load_cc_settings,
)

warnings.warn(
    "chimera.config.cc_settings is deprecated; "
    "import from chimera.mink.settings instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "CCSettings",
    "CCSettingsError",
    "MinkSettings",
    "MinkSettingsError",
    "Permissions",
    "load_cc_settings",
    "load_mink_settings",
]

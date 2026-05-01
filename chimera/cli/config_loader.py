"""Helpers for reading persistent CLI defaults from ``~/.chimera/config.toml``.

This module exposes a single helper, :func:`resolve_default`, that any CLI
(mink/otter/ferret/weasel/shrew/stoat/badger or the top-level ``chimera``
parser itself) can use to look up a user-configured default before falling
back to its built-in value.

The schema is intentionally simple: a flat TOML file whose top-level tables
correspond to CLI namespaces (``[otter]``, ``[mink]``, …) plus a ``[global]``
section for cross-cutting defaults. Keys inside each table are the same flag
names a user would pass on the command line, with dashes preserved::

    [global]
    no-color = true

    [otter]
    model = "glm-5"

    [mink]
    permission-mode = "auto"

Stdlib only — uses :mod:`tomllib` for reads. Writes are delegated to
:mod:`chimera.cli.config_cmd`.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

#: Default path to the chimera config file. Honors ``$CHIMERA_CONFIG_HOME``
#: (a directory) and ``$HOME`` so tests can redirect both. Tests that set
#: ``$HOME`` to a ``tmp_path`` will see writes/reads land under that root.
def config_path() -> Path:
    """Return the on-disk location of ``config.toml``.

    Resolution order:

    1. ``$CHIMERA_CONFIG_HOME`` — explicit override (a directory containing
       ``config.toml``). Useful in tests and CI sandboxes.
    2. ``$HOME/.chimera/config.toml`` — the canonical location.

    The path is **not** required to exist; callers that read should treat a
    missing file as "no defaults configured".
    """
    override = os.environ.get("CHIMERA_CONFIG_HOME")
    if override:
        return Path(override) / "config.toml"
    return Path(os.path.expanduser("~")) / ".chimera" / "config.toml"


def load_config() -> dict[str, Any]:
    """Read ``config.toml`` and return its parsed contents.

    Returns an empty dict if the file is missing or cannot be parsed. Errors
    are swallowed deliberately — a stale or corrupt config file should never
    prevent a CLI from starting; users will see their fallbacks instead.
    """
    path = config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def resolve_default(cli: str, key: str, fallback: Any = None) -> Any:
    """Look up a user default for ``cli.key`` with a fallback.

    Examples::

        model = resolve_default("otter", "model", "glm-5")
        no_color = resolve_default("global", "no-color", False)

    The lookup checks the ``[<cli>]`` table for ``key`` first. If absent and
    ``cli != "global"``, it then checks ``[global]`` so cross-cutting flags
    set once apply everywhere. The provided ``fallback`` is returned only
    when neither table contains the key.

    Args:
        cli: TOML table name (e.g. ``"otter"``, ``"mink"``, ``"global"``).
        key: Dot-free key name within the table.
        fallback: Value to return when the key is unconfigured.

    Returns:
        The configured value or ``fallback``.
    """
    data = load_config()
    table = data.get(cli)
    if isinstance(table, dict) and key in table:
        return table[key]
    if cli != "global":
        global_table = data.get("global")
        if isinstance(global_table, dict) and key in global_table:
            return global_table[key]
    return fallback


__all__ = ["config_path", "load_config", "resolve_default"]

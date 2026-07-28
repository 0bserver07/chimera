"""Unified reader for the user TUI + skills configuration (one loader, one chain).

Historically the TUI read its settings through two different dialects:
keybindings from a TOML ``config.toml`` (via :mod:`chimera.cli.config_loader`)
and the status line / skills toggles from a ``config.{yaml,yml,json}`` walked
across a *different* set of scopes. This module collapses that split into a
single reader that accepts **any** of ``config.toml`` / ``config.yaml`` /
``config.yml`` / ``config.json`` in every scope, deep-merges them under one
documented precedence chain, and serves all of those consumers.

Precedence, lowest first (a higher scope overrides a lower one, key-by-key):

1. **XDG user scope** — ``<home>/.config/chimera/``
2. **User scope** — ``<home>/.chimera/`` (or ``$CHIMERA_CONFIG_HOME`` when set)
3. **Project scope** — ``<project>/.chimera/``

Within a single scope, every present ``config.*`` file is deep-merged with
``config.toml`` (the canonical format) taking precedence over the YAML/JSON
forms on a key collision. Missing files and parse errors degrade to an empty
contribution — a broken or stale config file never blocks startup.

**Canonical format: TOML** at ``~/.chimera/config.toml`` — the same file the
codename CLIs already read their persistent defaults from, and the only format
that parses with the standard library alone (:mod:`tomllib`), honoring the
zero-dependency-core rule. New configuration should be written there; the
YAML/JSON scopes remain a read-time compatibility shim so files written against
the older status-line loader keep loading unchanged.

Consumers historically read different slices of this chain, and each keeps its
exact prior discovery so behavior is byte-identical when no config is present:

- keybindings (:func:`chimera.tui.keys.load_user_keybinds`) and skills toggles
  (:func:`chimera.skills.discovery.resolve_foreign_config`) read the **user
  scope** via :func:`load_user_scope_config` (honoring ``$CHIMERA_CONFIG_HOME``);
- the status line (:func:`chimera.tui.statusline.load_tui_config`) reads the
  **full** XDG/user/project chain via :func:`load_tui_config`, anchored on an
  explicit ``home`` rather than ``$CHIMERA_CONFIG_HOME``;
- storage (:mod:`chimera.config.paths`) reads the same full chain via
  :func:`load_storage_config` for the ``[storage]`` root and per-store
  retention.

Stdlib only. YAML/JSON parsing is delegated to
:class:`chimera.config.config_file.ChimeraConfig`, which itself falls back to a
minimal built-in parser when PyYAML is absent.
"""
from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from chimera.config.paths import project_state_dir, user_scope_dir

__all__ = [
    "CONFIG_BASENAMES",
    "config_home_dir",
    "config_scopes",
    "load_merged_config",
    "load_section",
    "load_storage_config",
    "load_tui_config",
    "load_user_scope_config",
    "tui_config_scopes",
]

#: Recognized config basenames within one scope, in ascending precedence order.
#: ``config.toml`` is the canonical format and wins a key collision within a
#: scope; the YAML/JSON forms are a backward-compatibility shim.
CONFIG_BASENAMES: tuple[str, ...] = (
    "config.json",
    "config.yml",
    "config.yaml",
    "config.toml",
)


def config_home_dir(home: str | os.PathLike[str] | None = None) -> Path:
    """Return the user-scope config directory (holds the canonical TOML).

    ``$CHIMERA_CONFIG_HOME`` (a directory) overrides everything — the same
    override :func:`chimera.cli.config_loader.config_path` honors — otherwise
    the directory is ``<home>/.chimera``.

    Args:
        home: Home-directory override (tests). Ignored when
            ``$CHIMERA_CONFIG_HOME`` is set.

    Returns:
        The user-scope config directory.
    """
    override = os.environ.get("CHIMERA_CONFIG_HOME")
    if override:
        return Path(override)
    return user_scope_dir(home)


def _read_config_file(path: Path) -> dict[str, Any]:
    """Read one config file of any supported format; ``{}`` on any failure.

    ``.toml`` is parsed with the stdlib :mod:`tomllib`; ``.yaml`` / ``.yml`` /
    ``.json`` are delegated to :class:`chimera.config.config_file.ChimeraConfig`
    (which itself degrades gracefully when PyYAML is unavailable). Any error —
    a missing file, a parse failure, a non-mapping top level — yields ``{}`` so
    that a broken config can never take a caller down.
    """
    if not path.is_file():
        return {}
    try:
        if path.suffix == ".toml":
            with path.open("rb") as handle:
                data: Any = tomllib.load(handle)
        else:
            from chimera.config.config_file import ChimeraConfig

            data = ChimeraConfig.from_file(path).data
    except Exception:  # a broken config must never crash startup
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` into ``base`` (overlay wins); return base.

    Nested mappings merge key-by-key, so a higher scope that sets
    ``tui.status_line`` does not erase a lower scope's ``tui.keybinds``. Any
    non-mapping value (scalar or list) replaces wholesale.
    """
    for key, value in overlay.items():
        existing = base.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            _deep_merge(existing, value)
        else:
            base[key] = value
    return base


def _load_scope_dir(scope: Path) -> dict[str, Any]:
    """Deep-merge every present ``config.*`` in one directory (TOML wins)."""
    merged: dict[str, Any] = {}
    for basename in CONFIG_BASENAMES:
        data = _read_config_file(scope / basename)
        if data:
            _deep_merge(merged, data)
    return merged


def load_merged_config(scopes: Iterable[str | os.PathLike[str]]) -> dict[str, Any]:
    """Deep-merge the config found across ``scopes`` (lowest to highest).

    Args:
        scopes: Directories to read, in ascending precedence order. Each is
            scanned for the supported ``config.*`` basenames.

    Returns:
        The merged top-level config mapping (``{}`` when nothing is present).
    """
    merged: dict[str, Any] = {}
    for scope in scopes:
        data = _load_scope_dir(Path(scope))
        if data:
            _deep_merge(merged, data)
    return merged


def load_user_scope_config(*, home: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load the merged config from the single user scope (CLI-side chain).

    This is exactly the scope the codename CLIs, keybindings, and skills
    toggles read: ``$CHIMERA_CONFIG_HOME`` (when set) else ``<home>/.chimera``.
    It now reads every supported format in that directory, not only
    ``config.toml`` — additive, so existing TOML files load identically.

    Args:
        home: Home-directory override (tests). Ignored when
            ``$CHIMERA_CONFIG_HOME`` is set.

    Returns:
        The merged top-level config mapping (``{}`` when nothing is present).
    """
    return _load_scope_dir(config_home_dir(home))


def config_scopes(
    project_dir: str | os.PathLike[str] | None = None,
    *,
    home: str | os.PathLike[str] | None = None,
) -> list[Path]:
    """Return the full scope chain (XDG < user < project).

    Unlike the CLI-side user scope, this deliberately ignores
    ``$CHIMERA_CONFIG_HOME`` and anchors on ``home`` — preserving the status
    line's historical discovery so callers that pass an explicit ``home`` (and
    tests) behave identically.

    The user scope is :func:`chimera.config.paths.user_scope_dir`, the fixed
    ``<home>/.chimera`` anchor rather than the (relocatable) storage root: this
    chain is where ``[storage] root`` is *read from*, so resolving it through
    that setting would be circular.

    Args:
        project_dir: Project root (default: cwd).
        home: Home directory (default: the real home directory).

    Returns:
        The scope directories, ascending precedence order.
    """
    base = Path(home) if home is not None else Path.home()
    project = Path(project_dir) if project_dir is not None else Path.cwd()
    return [base / ".config" / "chimera", user_scope_dir(base), project_state_dir(project)]


def tui_config_scopes(
    project_dir: str | os.PathLike[str] | None = None,
    *,
    home: str | os.PathLike[str] | None = None,
) -> list[Path]:
    """Return the status-line scope chain (XDG < user < project).

    Retained under its original name for the status-line callers; identical to
    :func:`config_scopes`, which is the section-neutral spelling.

    Args:
        project_dir: Project root (default: cwd).
        home: Home directory (default: the real home directory).

    Returns:
        The scope directories, ascending precedence order.
    """
    return config_scopes(project_dir, home=home)


def load_section(
    section: str,
    project_dir: str | os.PathLike[str] | None = None,
    *,
    home: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Load one top-level config section across the XDG/user/project chain.

    Args:
        section: Top-level table name (``"tui"``, ``"storage"``, …).
        project_dir: Project root (default: cwd).
        home: Home-directory override (tests).

    Returns:
        The section as a dict (``{}`` when absent or not a table).
    """
    merged = load_merged_config(config_scopes(project_dir, home=home))
    table = merged.get(section)
    return dict(table) if isinstance(table, dict) else {}


def load_tui_config(
    project_dir: str | os.PathLike[str] | None = None,
    *,
    home: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Load the merged ``tui`` config section across the XDG/user/project chain.

    Args:
        project_dir: Project root (default: cwd).
        home: Home-directory override (tests).

    Returns:
        The ``tui`` section as a dict (``{}`` when absent).
    """
    return load_section("tui", project_dir, home=home)


def load_storage_config(
    project_dir: str | os.PathLike[str] | None = None,
    *,
    home: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Load the merged ``storage`` config section across the same chain.

    The section carries the storage root and per-store retention::

        [storage]
        root = "~/.chimera"        # optional; $CHIMERA_HOME wins

        [storage.sessions]
        retain = 200               # keep newest N   (absent = keep forever)
        max-age-days = 90          # and/or drop older than this

    Consumed by :mod:`chimera.config.paths`: ``root`` by
    :func:`~chimera.config.paths.chimera_home`, the per-store tables by
    :func:`~chimera.config.paths.store_retention`. Retention is read here and
    acted on only by ``chimera gc`` — declaring it prunes nothing.

    Args:
        project_dir: Project root (default: cwd).
        home: Home-directory override (tests).

    Returns:
        The ``storage`` section as a dict (``{}`` when absent).
    """
    return load_section("storage", project_dir, home=home)

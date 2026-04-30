"""``chimera.ferret.config`` — read upstream-style TOML config files.

The upstream IDE-first OpenAI-flagship coding agent stores its user-scope
configuration at ``~/.codex/config.toml`` and supports an optional
project-scope override at ``./.codex/config.toml`` (relative to the
working directory). This module ingests both files using the stdlib
``tomllib`` parser and merges them with the project file taking
precedence — matching the upstream's "project overrides user" rule.

The reader is **defensive by design**: a missing file, a parse error, or
a non-mapping top-level structure all degrade to an empty dict so the
ferret CLI never crashes on a malformed config. Callers that want strict
validation can re-parse via ``tomllib.loads`` themselves.

Trademark hygiene: filesystem path mentions like ``~/.codex/config.toml``
are filesystem facts, not brand claims, and are explicitly allowed by
``research/ferret/SPEC.md``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_USER_CONFIG_PATH",
    "DEFAULT_PROJECT_CONFIG_NAME",
    "FerretConfig",
    "load_config",
    "load_user_config",
    "load_project_config",
    "merge_configs",
]

DEFAULT_USER_CONFIG_PATH = "~/.codex/config.toml"
"""User-scope TOML config path (filesystem fact).

Resolved with :func:`os.path.expanduser` at call time so test
monkeypatches of ``HOME`` / ``Path.home`` are honored. The upstream
IDE-first OpenAI-flagship coding agent uses this same path; we read
it as a compatibility convenience so existing user configs continue
to work under ferret.
"""

DEFAULT_PROJECT_CONFIG_NAME = ".codex/config.toml"
"""Project-scope TOML config relative to the working directory.

When the project root contains ``.codex/config.toml``, its keys
override the user-scope file — matching the upstream's "project
overrides user" rule.
"""


# ---------------------------------------------------------------------------
# Stdlib tomllib import — Python 3.11+
# ---------------------------------------------------------------------------


def _load_tomllib() -> Any:
    """Return the stdlib ``tomllib`` module, raising on Python < 3.11.

    Kept as a function so callsites can degrade gracefully if a future
    refactor lands the ferret module on a non-stdlib TOML reader.
    """
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "chimera.ferret.config requires Python 3.11+ for tomllib"
        ) from exc
    return tomllib


# ---------------------------------------------------------------------------
# FerretConfig dataclass
# ---------------------------------------------------------------------------


class FerretConfig(dict[str, Any]):
    """Materialized ferret config — a thin ``dict`` subclass.

    Storing the merged config as a dict keeps callers free to walk it
    with ordinary ``cfg.get("key")`` calls. The dataclass-style access
    via attribute is intentionally avoided — TOML keys can contain
    dots, and dotted lookups belong to the consumer (sandbox /
    approval / providers modules) not this loader.

    Useful indirection: a downstream sibling agent (FF6) can subclass
    :class:`FerretConfig` to layer typed accessors on top without
    breaking the dict contract.
    """

    @property
    def user_path(self) -> Path | None:
        """Return the resolved user-scope path, or ``None`` if absent."""
        return self.get("__user_path__")

    @property
    def project_path(self) -> Path | None:
        """Return the resolved project-scope path, or ``None`` if absent."""
        return self.get("__project_path__")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _read_toml(path: Path) -> dict[str, Any]:
    """Read and parse a TOML file at ``path``; return ``{}`` on any error.

    The returned dict is a fresh top-level mapping — keys are the
    TOML table headers; values are whatever ``tomllib`` decoded.

    Args:
        path: Absolute path to the TOML file. Need not exist.

    Returns:
        A dict (possibly empty) with the file's contents. Errors are
        swallowed and logged to stderr so the caller never crashes
        on a malformed config.
    """
    if not path.exists():
        return {}
    try:
        tomllib = _load_tomllib()
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError, RuntimeError) as exc:
        sys.stderr.write(
            f"[ferret] config read failed for {path}: {exc}\n"
        )
        sys.stderr.flush()
        return {}
    if not isinstance(data, dict):
        return {}
    return dict(data)


def load_user_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the user-scope TOML config.

    Args:
        path: Override the user-scope path. Defaults to
            :data:`DEFAULT_USER_CONFIG_PATH`. The argument is
            ``os.path.expanduser``-ed so ``~`` works.

    Returns:
        A dict (possibly empty) with the user config contents.
    """
    raw = path if path is not None else DEFAULT_USER_CONFIG_PATH
    resolved = Path(os.path.expanduser(str(raw))).resolve()
    return _read_toml(resolved)


def load_project_config(
    project_root: str | Path | None = None,
    *,
    name: str = DEFAULT_PROJECT_CONFIG_NAME,
) -> dict[str, Any]:
    """Load the project-scope TOML config.

    Args:
        project_root: Project root directory. Defaults to the current
            working directory. Resolved with :func:`os.path.abspath`.
        name: Path *relative to* ``project_root``. Defaults to
            :data:`DEFAULT_PROJECT_CONFIG_NAME`.

    Returns:
        A dict (possibly empty) with the project config contents.
    """
    root = Path(os.path.abspath(str(project_root or os.getcwd())))
    candidate = root / name
    return _read_toml(candidate)


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


def merge_configs(
    user: dict[str, Any], project: dict[str, Any],
) -> dict[str, Any]:
    """Merge two config dicts with *project* overriding *user*.

    The merge is **shallow-recursive**: keys present in both dicts are
    merged when both values are dicts; otherwise the project value
    wins. This matches the upstream's documented "project overrides
    user" semantics for nested tables (e.g. ``[providers.openai]``).

    Args:
        user: User-scope config dict.
        project: Project-scope config dict (wins on conflict).

    Returns:
        A new merged dict. Inputs are not mutated.
    """
    result: dict[str, Any] = dict(user)
    for key, project_val in project.items():
        user_val = result.get(key)
        if isinstance(project_val, dict) and isinstance(user_val, dict):
            result[key] = merge_configs(user_val, project_val)
        else:
            result[key] = project_val
    return result


def load_config(
    project_root: str | Path | None = None,
    *,
    user_path: str | Path | None = None,
    project_name: str = DEFAULT_PROJECT_CONFIG_NAME,
    explicit_path: str | Path | None = None,
) -> FerretConfig:
    """Load and merge the ferret TOML config.

    Reads ``~/.codex/config.toml`` (user scope) and
    ``<project_root>/.codex/config.toml`` (project scope), merging
    them with the project file winning on conflict. When
    ``explicit_path`` is set (e.g. via ``chimera ferret --config FILE``),
    that file replaces *both* defaults: the override is a complete
    config, not a third layer.

    Args:
        project_root: Project root directory. Defaults to the
            current working directory.
        user_path: Override the user-scope config path. Defaults to
            :data:`DEFAULT_USER_CONFIG_PATH`.
        project_name: Project-scope filename relative to
            ``project_root``. Defaults to
            :data:`DEFAULT_PROJECT_CONFIG_NAME`.
        explicit_path: When non-``None``, this file's contents are
            returned verbatim (modulo defensive empty-on-error
            behaviour). User and project paths are skipped.

    Returns:
        A :class:`FerretConfig` (dict subclass) with the merged
        contents plus ``__user_path__`` / ``__project_path__``
        bookkeeping keys for diagnostics. Empty when neither file
        exists.
    """
    if explicit_path is not None:
        resolved = Path(os.path.expanduser(str(explicit_path))).resolve()
        merged: dict[str, Any] = _read_toml(resolved)
        cfg = FerretConfig(merged)
        cfg["__user_path__"] = None
        cfg["__project_path__"] = resolved if resolved.exists() else None
        return cfg

    user_data = load_user_config(user_path)
    project_data = load_project_config(project_root, name=project_name)

    user_resolved: Path | None = None
    if user_path is not None or DEFAULT_USER_CONFIG_PATH:
        candidate_user = Path(
            os.path.expanduser(str(user_path or DEFAULT_USER_CONFIG_PATH))
        ).resolve()
        if candidate_user.exists():
            user_resolved = candidate_user

    project_resolved: Path | None = None
    candidate_project = (
        Path(os.path.abspath(str(project_root or os.getcwd()))) / project_name
    )
    if candidate_project.exists():
        project_resolved = candidate_project

    merged_dict = merge_configs(user_data, project_data)
    cfg = FerretConfig(merged_dict)
    cfg["__user_path__"] = user_resolved
    cfg["__project_path__"] = project_resolved
    return cfg

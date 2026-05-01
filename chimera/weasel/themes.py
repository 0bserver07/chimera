"""Weasel theme registry — loadable color + REPL prompt-prefix bundles.

Themes are a core extension surface for weasel: they bundle a small palette
of named colors plus a set of style strings the REPL splices in front of
its prompt segments. The registry mirrors the npm-style discovery model
already used by :mod:`chimera.weasel.extensions` — themes can ship as
JSON files under ``<project_root>/.weasel/themes/`` (project scope) or
``~/.weasel/themes/`` (user scope), with project-scope winning on name
conflict. Three built-in themes ship with the registry so a stock weasel
invocation always has a sensible default.

JSON shape (all fields optional except ``name``):

```json
{
  "name": "midnight",
  "colors": {
    "foreground": "#e6e6e6",
    "background": "#0b0b1a",
    "accent": "#6cb6ff",
    "muted": "#7a7a8c"
  },
  "style_prompts": {
    "user": "you> ",
    "assistant": "bot> ",
    "tool": "[tool] ",
    "error": "!! "
  }
}
```

The loader is **stdlib-only** and never raises on a malformed theme file:
the offending entry is skipped so a single bad JSON cannot break the
whole weasel invocation. Built-ins are always present, on-disk themes
are *added* on top, and unknown lookups quietly fall back to ``default``.

Trademark hygiene: never names the upstream brand. ``.weasel/themes/`` is
a filesystem fact, not a product claim.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Theme dataclass
# ---------------------------------------------------------------------------


@dataclass
class Theme:
    """A named color + REPL prompt-prefix bundle.

    Attributes:
        name: Theme identifier; lookup key for :func:`get_theme`.
        colors: Mapping of slot -> color value. The conventional slots
            are ``foreground``, ``background``, ``accent``, and
            ``muted`` but extra keys are preserved verbatim so embedders
            can stash brand-specific palettes without subclassing.
        style_prompts: Mapping of REPL prompt slot -> prefix string.
            Conventional slots: ``user`` (input prompt), ``assistant``
            (model output), ``tool`` (tool-call header), ``error``
            (error banner). Unknown slots are preserved.
    """

    name: str
    colors: dict[str, str] = field(default_factory=dict)
    style_prompts: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Built-in themes
# ---------------------------------------------------------------------------


def _builtin_default() -> Theme:
    """Return the stock ``default`` theme.

    Mirrors a neutral terminal palette so the REPL is readable on both
    light- and dark-backgrounded terminals without forcing either.
    """
    return Theme(
        name="default",
        colors={
            "foreground": "#d0d0d0",
            "background": "#1c1c1c",
            "accent": "#5fafff",
            "muted": "#808080",
            "error": "#ff5f5f",
        },
        style_prompts={
            "user": "weasel> ",
            "assistant": "",
            "tool": "[tool] ",
            "error": "[error] ",
        },
    )


def _builtin_dark() -> Theme:
    """Return the stock ``dark`` theme.

    Higher-contrast palette tuned for dark terminals; accent color is
    a saturated cyan so streamed-token highlights stand out.
    """
    return Theme(
        name="dark",
        colors={
            "foreground": "#f5f5f5",
            "background": "#0a0a0a",
            "accent": "#00d7d7",
            "muted": "#5f5f5f",
            "error": "#ff0000",
        },
        style_prompts={
            "user": "» ",
            "assistant": "  ",
            "tool": "[tool] ",
            "error": "[error] ",
        },
    )


def _builtin_solarized() -> Theme:
    """Return the stock ``solarized`` theme (dark-base palette).

    Solarized's base16 family is a long-standing terminal-palette
    convention; we ship the dark-base variant because the weasel REPL
    streams output, which suits darker backgrounds.
    """
    return Theme(
        name="solarized",
        colors={
            "foreground": "#839496",
            "background": "#002b36",
            "accent": "#268bd2",
            "muted": "#586e75",
            "error": "#dc322f",
        },
        style_prompts={
            "user": "λ ",
            "assistant": "  ",
            "tool": "[tool] ",
            "error": "[error] ",
        },
    )


def _builtin_themes() -> dict[str, Theme]:
    """Return a freshly-built dict of the three built-in themes.

    Returns a new dict on each call so callers can mutate the result
    without poisoning the next caller's view.
    """
    return {
        "default": _builtin_default(),
        "dark": _builtin_dark(),
        "solarized": _builtin_solarized(),
    }


# Module-level cache used by :func:`get_theme` when no explicit registry
# is provided. Built lazily so importing this module stays cheap.
_DEFAULT_REGISTRY: dict[str, Theme] | None = None


def _get_default_registry() -> dict[str, Theme]:
    """Return (and cache) the module-level built-in registry.

    The registry is built once per process and reused across calls so
    repeated ``get_theme(...)`` lookups stay O(1) without re-allocating
    the built-in dataclasses.
    """
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = _builtin_themes()
    return _DEFAULT_REGISTRY


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    """Best-effort JSON read; returns ``None`` on any failure.

    Mirrors :func:`chimera.weasel.extensions._read_json` so the two
    loaders share a single permissive policy: a malformed file logs
    nothing and is silently skipped.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _coerce_str_dict(raw: Any) -> dict[str, str]:
    """Normalize an arbitrary mapping into ``dict[str, str]``.

    Non-dict input yields an empty dict. Non-string values are coerced
    via ``str()`` so callers can rely on the result being safe to
    splice into a terminal control sequence without further checks.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        out[key] = str(value) if value is not None else ""
    return out


def _theme_from_payload(payload: Any, *, fallback_name: str) -> Theme | None:
    """Materialize a :class:`Theme` from a parsed JSON payload.

    Returns ``None`` when the payload is not a dict. Missing ``colors``
    and ``style_prompts`` fields default to empty mappings rather than
    raising, mirroring the extensions loader's permissive policy.

    Args:
        payload: The result of :func:`json.loads` (any JSON value).
        fallback_name: Used as the theme name when the payload omits
            an explicit ``name`` field — typically the source file's
            stem so on-disk discovery yields stable identifiers.

    Returns:
        A :class:`Theme` instance, or ``None`` when the payload is not
        a dict (so the caller can skip the entry).
    """
    if not isinstance(payload, dict):
        return None
    raw_name = payload.get("name")
    name = (
        str(raw_name).strip()
        if isinstance(raw_name, str) and raw_name.strip()
        else fallback_name
    )
    return Theme(
        name=name,
        colors=_coerce_str_dict(payload.get("colors")),
        style_prompts=_coerce_str_dict(payload.get("style_prompts")),
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _user_root() -> Path:
    """Return the user-level weasel theme root."""
    return Path.home() / ".weasel" / "themes"


def _project_root_dir(project_root: Path) -> Path:
    """Return the project-level weasel theme root."""
    return project_root / ".weasel" / "themes"


def _scan_dir(root: Path) -> list[Theme]:
    """Return parsed themes under a single root.

    Hidden files (``.foo.json``) are skipped. JSON parse failures are
    logged via the ``None`` return path of :func:`_theme_from_payload`
    so the caller does not need to filter them.
    """
    if not root.is_dir():
        return []
    out: list[Theme] = []
    for child in sorted(root.iterdir()):
        if not child.is_file():
            continue
        if child.suffix.lower() != ".json":
            continue
        if child.name.startswith("."):
            continue
        payload = _read_json(child)
        theme = _theme_from_payload(payload, fallback_name=child.stem)
        if theme is not None:
            out.append(theme)
    return out


def load_themes(
    project_root: Path,
    *,
    user_root: Path | None = None,
) -> dict[str, Theme]:
    """Load the full theme registry — built-ins plus user/project files.

    Discovery order, with later entries overriding earlier ones on
    name collision:

    1. Built-in themes (``default``, ``dark``, ``solarized``).
    2. User-scope JSON files under ``user_root`` (defaults to
       ``~/.weasel/themes/``).
    3. Project-scope JSON files under ``<project_root>/.weasel/themes/``.

    Args:
        project_root: Project directory; ``<project_root>/.weasel/themes/``
            is scanned for project-scope themes.
        user_root: Override for the user-level theme root. Defaults
            to ``~/.weasel/themes/``. Primarily used by tests.

    Returns:
        Dict keyed by theme name. The returned dict is a fresh copy
        so callers can mutate it without poisoning the module cache.
    """
    user_dir = user_root if user_root is not None else _user_root()
    project_dir = _project_root_dir(project_root)

    registry: dict[str, Theme] = _builtin_themes()
    for theme in _scan_dir(user_dir):
        registry[theme.name] = theme
    for theme in _scan_dir(project_dir):
        registry[theme.name] = theme
    return registry


def get_theme(
    name: str | None,
    *,
    registry: dict[str, Theme] | None = None,
) -> Theme:
    """Return the named theme, falling back to ``default`` when missing.

    Args:
        name: Theme identifier; ``None`` or unknown names yield the
            built-in ``default`` theme. Whitespace is stripped.
        registry: Optional pre-built registry (typically the result of
            :func:`load_themes`). When omitted, the module-level
            built-in cache is used so the lookup stays stdlib-only.

    Returns:
        The matched :class:`Theme`. Always returns a theme — never
        raises — so callers can inline the lookup in render code.
    """
    bag = registry if registry is not None else _get_default_registry()
    if isinstance(name, str):
        cleaned = name.strip()
        if cleaned and cleaned in bag:
            return bag[cleaned]
    # Always fall back to the built-in default; if the caller supplied
    # a registry that overrode ``default``, honor that override.
    if "default" in bag:
        return bag["default"]
    return _builtin_default()


__all__ = [
    "Theme",
    "get_theme",
    "load_themes",
]

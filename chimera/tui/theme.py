"""Semantic slot themes for the Chimera TUIs (R-THEME-1..4).

One registry — :data:`SLOTS` — names every *semantic* color the frontends can
paint with (base chrome, markdown, syntax, diff, tool rows, plus opacity
knobs). Nothing addresses a widget: a theme maps slots, so a palette swap never
has to know what a lane pane is.

The pieces, all stdlib-only and widget-free (rich/textual are never imported
here, so this module and its tests run in CI's no-``tui``-extra posture):

- :class:`Theme` — a named slot map with an optional ``vars`` palette that
  slots reference by ``$name`` (resolved with circular-reference detection).
  Every value may be a plain style string or a ``{dark, light}`` variant map.
- :class:`Palette` — a *resolved* theme: slot → style string, quantized to the
  terminal's color depth. :meth:`Palette.style` is what renderers call.
- :func:`detect_mode` / :func:`color_depth` — the R-THEME-2/4 detection
  cascades (explicit config → env hints → terminal-background luminance →
  dark; truecolor → 256 → 16 → none, honoring ``NO_COLOR``).
- :class:`ThemeSettings` — the resolved ``[tui]`` configuration
  (``theme``, ``theme_mode``, ``animations``) plus :meth:`ThemeSettings.palette`.
- :func:`discover_themes` — user theme files under the config chain's
  ``themes/`` directories (``~/.config/chimera/themes/``, ``~/.chimera/themes/``,
  ``<project>/.chimera/themes/``), layered defaults < user < project.

**Additive by construction.** The built-in ``default`` theme's slot values are
exactly the style strings the shipped renderers hardcoded before themes
existed, so an unconfigured TUI renders byte-identically; a theme only takes
effect when ``[tui] theme`` names one.

Config::

    [tui]
    theme = "chimera"       # default | chimera | mono | <user theme name>
    theme_mode = "auto"     # auto | dark | light | lock
    animations = true       # false → static spinners/heartbeats

A user theme file (``~/.chimera/themes/midnight.toml``)::

    description = "cool dark"
    [vars]
    ink = { dark = "#c8d3f5", light = "#2a2f45" }
    [slots]
    base.text = "$ink"
    diff.add = "#7fd88f"
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "BUILTIN_THEMES",
    "DEFAULT_THEME",
    "OPACITY_KNOBS",
    "SLOTS",
    "Palette",
    "SlotDef",
    "Theme",
    "ThemeError",
    "ThemeSettings",
    "color_depth",
    "detect_mode",
    "discover_themes",
    "load_theme_settings",
    "luminance",
    "quantize",
    "slot_ids",
]


class ThemeError(ValueError):
    """A theme is malformed (unknown slot, circular ``vars`` reference, …)."""


@dataclass(frozen=True)
class SlotDef:
    """One semantic slot in the theme schema.

    Args:
        slot_id: Dotted name (``diff.add``), the key themes assign.
        family: Grouping for ``/theme`` and docs (``base``, ``markdown``,
            ``syntax``, ``diff``, ``tool``, ``chrome``, ``status``).
        description: One-line meaning.
        dark: Built-in value in dark mode.
        light: Built-in value in light mode (defaults to *dark* when the
            value is terminal-palette-native and works in both).
    """

    slot_id: str
    family: str
    description: str
    dark: str
    light: str = ""

    def default(self, mode: str) -> str:
        """The built-in value for *mode* (``light`` falls back to ``dark``)."""
        if mode == "light" and self.light:
            return self.light
        return self.dark


#: The semantic slot schema (R-THEME-1). Values here are the *built-in*
#: defaults and deliberately reproduce the pre-theme hardcoded styles for every
#: slot the shipped renderers already used, so no config == no visual change.
SLOTS: tuple[SlotDef, ...] = (
    # -- base ------------------------------------------------------------
    SlotDef("base.primary", "base", "headline chrome (status bar background)", "blue"),
    SlotDef("base.accent", "base", "focused accents", "cyan"),
    SlotDef("base.text", "base", "body foreground", ""),
    SlotDef("base.muted", "base", "secondary text", "dim"),
    SlotDef("base.dim", "base", "de-emphasized chrome", "dim"),
    SlotDef("base.background", "base", "app background", "", "white"),
    SlotDef("base.surface", "base", "panel/dialog background", ""),
    SlotDef("base.panel", "base", "secondary panel background", ""),
    SlotDef("base.border", "base", "resting border", "cyan"),
    SlotDef("base.border-focus", "base", "focused border", "bright_cyan"),
    SlotDef("base.selection", "base", "selected text", "reverse"),
    # -- status ----------------------------------------------------------
    SlotDef("status.error", "status", "errors and failures", "red"),
    SlotDef("status.warning", "status", "warnings, near-cap meters", "yellow"),
    SlotDef("status.success", "status", "success, winning lane", "green"),
    SlotDef("status.info", "status", "informational notes", "cyan"),
    SlotDef("status.busy", "status", "a turn is running", "yellow"),
    SlotDef("status.idle", "status", "idle / done", "dim"),
    # -- chrome (transcript grammar) -------------------------------------
    SlotDef("chrome.gutter-user", "chrome", "the user echo glyph", "bold cyan"),
    SlotDef("chrome.user-text", "chrome", "the user's echoed prompt", "bold"),
    SlotDef("chrome.gutter-assistant", "chrome", "assistant block gutter", ""),
    SlotDef("chrome.note", "chrome", "frontend notes (steer, queued)", "magenta"),
    SlotDef("chrome.elision", "chrome", "the '… +N lines …' marker", "dim"),
    SlotDef("chrome.reasoning", "chrome", "revealed reasoning text", "dim italic"),
    SlotDef("chrome.reasoning-trace", "chrome", "the collapsed thinking trace", "dim"),
    SlotDef("chrome.heartbeat", "chrome", "the live thinking heartbeat", "dim"),
    SlotDef("chrome.rule", "chrome", "turn separators", "dim"),
    SlotDef("chrome.result", "chrome", "the turn result line", "dim"),
    # -- tool rows / cards -----------------------------------------------
    SlotDef("tool.icon", "tool", "per-tool glyph", "yellow"),
    SlotDef("tool.name", "tool", "tool name / verb", "bold yellow"),
    SlotDef("tool.args", "tool", "one-line argument summary", "dim"),
    SlotDef("tool.ok", "tool", "successful tool output", "green"),
    SlotDef("tool.error", "tool", "failed tool output", "red"),
    SlotDef("tool.card", "tool", "block-card gutter", "dim"),
    # -- markdown ---------------------------------------------------------
    SlotDef("markdown.h1", "markdown", "level-1 heading", "bold"),
    SlotDef("markdown.h2", "markdown", "level-2 heading", "bold"),
    SlotDef("markdown.h3", "markdown", "level-3+ heading", "bold"),
    SlotDef("markdown.emphasis", "markdown", "italic emphasis", "italic"),
    SlotDef("markdown.strong", "markdown", "bold emphasis", "bold"),
    SlotDef("markdown.code", "markdown", "inline code", "cyan"),
    SlotDef("markdown.code-block", "markdown", "fenced code background", ""),
    SlotDef("markdown.link", "markdown", "links", "underline blue", "underline blue"),
    SlotDef("markdown.quote", "markdown", "block quotes", "dim italic"),
    SlotDef("markdown.list", "markdown", "list bullets", ""),
    SlotDef("markdown.rule", "markdown", "horizontal rules / heading rules", "dim"),
    # -- syntax (terminal-palette by default, R-REN-4) --------------------
    SlotDef("syntax.keyword", "syntax", "language keywords", "magenta"),
    SlotDef("syntax.string", "syntax", "string literals", "green"),
    SlotDef("syntax.number", "syntax", "numeric literals", "cyan"),
    SlotDef("syntax.comment", "syntax", "comments", "dim"),
    SlotDef("syntax.function", "syntax", "function names", "blue"),
    SlotDef("syntax.type", "syntax", "types and classes", "yellow"),
    SlotDef("syntax.operator", "syntax", "operators and punctuation", ""),
    # -- diff --------------------------------------------------------------
    SlotDef("diff.add", "diff", "added lines", "green"),
    SlotDef("diff.remove", "diff", "removed lines", "red"),
    SlotDef("diff.add-word", "diff", "changed tokens inside an added line",
            "reverse green"),
    SlotDef("diff.remove-word", "diff", "changed tokens inside a removed line",
            "reverse red"),
    SlotDef("diff.hunk", "diff", "@@ hunk headers", "cyan"),
    SlotDef("diff.meta", "diff", "diff/index/+++/--- headers", "bold"),
    SlotDef("diff.context", "diff", "unchanged context lines", ""),
    SlotDef("diff.filename", "diff", "file names in the results screen", "bold cyan"),
)

#: Opacity knobs (R-THEME-1). Terminals have no alpha channel, so a knob below
#: :data:`_DIM_BELOW` renders as the terminal's ``dim`` attribute — honest
#: degradation rather than a fake blend.
OPACITY_KNOBS: dict[str, float] = {
    "reasoning": 0.6,   # collapsed/revealed reasoning dimming
    "chrome": 0.75,     # notes, rules, result lines
    "inactive": 0.6,    # unfocused lane panes
}

#: Below this, an opacity knob degrades to the ``dim`` attribute.
_DIM_BELOW = 0.85

_BY_SLOT: dict[str, SlotDef] = {s.slot_id: s for s in SLOTS}


def slot_ids() -> tuple[str, ...]:
    """Every slot id in schema order (drives docs, ``/theme``, validation)."""
    return tuple(s.slot_id for s in SLOTS)


# --------------------------------------------------------------------------
# Themes
# --------------------------------------------------------------------------
def _is_variant(value: Any) -> bool:
    """True when *value* is a ``{dark, light}`` variant map (not a namespace)."""
    return isinstance(value, Mapping) and bool(value) and set(value) <= {"dark", "light"}


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested slot tables into dotted ids.

    TOML's ``[slots]`` section writes ``diff.add = "green"`` as a nested table;
    a ``{dark, light}`` map is a *value*, not a namespace, so it is never
    descended into.

    Args:
        data: The (possibly nested) mapping.
        prefix: Dotted prefix accumulated by recursion.

    Returns:
        A flat ``dotted_id -> value`` mapping.
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping) and not _is_variant(value):
            out.update(_flatten(value, f"{name}."))
        else:
            out[name] = value
    return out


def _pick(value: Any, mode: str) -> str:
    """Select a mode's value from a scalar or a ``{dark, light}`` variant map."""
    if _is_variant(value):
        chosen = value.get(mode)
        if chosen is None:
            chosen = value.get("dark" if mode == "light" else "light")
        return "" if chosen is None else str(chosen)
    return "" if value is None else str(value)


@dataclass(frozen=True)
class Theme:
    """A named semantic-slot theme (R-THEME-1).

    Args:
        name: Theme id, as used by ``[tui] theme``.
        description: One-line description for the ``/theme`` picker.
        vars: Named palette entries slots may reference as ``$name``. Values
            may themselves be ``{dark, light}`` maps or other ``$`` refs.
        slots: Slot assignments (dotted ids; nested tables are flattened).
        opacity: Overrides for :data:`OPACITY_KNOBS`.
        source: Where the theme came from (``builtin`` or a file path), shown
            in the picker.
    """

    name: str
    description: str = ""
    vars: Mapping[str, Any] = field(default_factory=dict)
    slots: Mapping[str, Any] = field(default_factory=dict)
    opacity: Mapping[str, float] = field(default_factory=dict)
    source: str = "builtin"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, name: str = "",
                  source: str = "builtin") -> Theme:
        """Build a theme from a parsed config mapping.

        Args:
            data: ``{description, vars, slots, opacity}``; unknown top-level
                keys are ignored so a theme file can carry comments/metadata.
            name: Theme id; falls back to ``data["name"]``.
            source: Provenance label (a file path for user themes).

        Returns:
            The theme.

        Raises:
            ThemeError: When ``slots`` names an id outside the schema, or when
                ``opacity`` carries a non-numeric value.
        """
        raw_slots = data.get("slots")
        slots = _flatten(raw_slots) if isinstance(raw_slots, Mapping) else {}
        unknown = sorted(set(slots) - set(_BY_SLOT))
        if unknown:
            raise ThemeError(
                f"theme {name or data.get('name', '?')!r}: unknown slot(s) "
                f"{', '.join(unknown)} — valid slots: {', '.join(slot_ids())}"
            )
        raw_vars = data.get("vars")
        raw_opacity = data.get("opacity")
        opacity: dict[str, float] = {}
        if isinstance(raw_opacity, Mapping):
            for knob, value in raw_opacity.items():
                try:
                    opacity[str(knob)] = float(value)
                except (TypeError, ValueError) as exc:
                    raise ThemeError(
                        f"theme {name!r}: opacity.{knob} must be a number "
                        f"(got {value!r})"
                    ) from exc
        return cls(
            name=str(name or data.get("name") or "theme"),
            description=str(data.get("description") or ""),
            vars=dict(raw_vars) if isinstance(raw_vars, Mapping) else {},
            slots=slots,
            opacity=opacity,
            source=source,
        )

    def _resolve_var(self, ref: str, mode: str, seen: tuple[str, ...]) -> str:
        """Resolve one ``$name`` reference, detecting circular chains."""
        key = ref[1:]
        if key in seen:
            chain = " → ".join(f"${s}" for s in (*seen, key))
            raise ThemeError(f"theme {self.name!r}: circular var reference {chain}")
        if key not in self.vars:
            raise ThemeError(
                f"theme {self.name!r}: unknown var {ref!r} "
                f"(defined: {', '.join(sorted(self.vars)) or 'none'})"
            )
        value = _pick(self.vars[key], mode)
        if value.startswith("$"):
            return self._resolve_var(value, mode, (*seen, key))
        return value

    def resolve(self, mode: str = "dark") -> dict[str, str]:
        """Resolve every slot for *mode*, filling gaps from the schema defaults.

        Args:
            mode: ``dark`` or ``light``.

        Returns:
            ``slot_id -> style string`` for every slot in :data:`SLOTS`.

        Raises:
            ThemeError: On an unknown or circular ``$var`` reference.
        """
        resolved: dict[str, str] = {}
        for slot in SLOTS:
            if slot.slot_id in self.slots:
                value = _pick(self.slots[slot.slot_id], mode)
                if value.startswith("$"):
                    value = self._resolve_var(value, mode, ())
            else:
                value = slot.default(mode)
            resolved[slot.slot_id] = value
        return resolved

    def opacities(self) -> dict[str, float]:
        """Opacity knobs with theme overrides applied."""
        knobs = dict(OPACITY_KNOBS)
        knobs.update({k: v for k, v in self.opacity.items() if k in knobs})
        return knobs


#: The built-in themes. ``default`` reproduces the pre-theme hardcoded styles
#: exactly (so an unconfigured TUI is byte-identical); ``chimera`` is the house
#: truecolor theme with dark/light variants; ``mono`` drops color entirely.
BUILTIN_THEMES: dict[str, Theme] = {
    "default": Theme(
        name="default",
        description="terminal palette — inherits your 16 ANSI colors (the default)",
    ),
    "chimera": Theme(
        name="chimera",
        description="the house theme — truecolor, dark/light variants",
        vars={
            "ink": {"dark": "#d6dbe5", "light": "#1c2230"},
            "muted": {"dark": "#8a93a6", "light": "#5d6577"},
            "bg": {"dark": "#11141b", "light": "#fbfbfd"},
            "surface": {"dark": "#181c25", "light": "#f2f3f7"},
            "panel": {"dark": "#1f2430", "light": "#e8eaf0"},
            "iris": {"dark": "#8f7ff0", "light": "#5b46c8"},
            "teal": {"dark": "#4fd6c9", "light": "#0f8f85"},
            "amber": {"dark": "#e5b567", "light": "#a3701a"},
            "rose": {"dark": "#f07178", "light": "#c02f37"},
            "leaf": {"dark": "#8fd67a", "light": "#2f7d20"},
            "sky": {"dark": "#7aa2f7", "light": "#2a5cbf"},
        },
        slots={
            "base.primary": "$iris",
            "base.accent": "$teal",
            "base.text": "$ink",
            "base.muted": "$muted",
            "base.dim": "$muted",
            "base.background": "$bg",
            "base.surface": "$surface",
            "base.panel": "$panel",
            "base.border": "$panel",
            "base.border-focus": "$teal",
            "status.error": "$rose",
            "status.warning": "$amber",
            "status.success": "$leaf",
            "status.info": "$sky",
            "status.busy": "$amber",
            "status.idle": "$muted",
            "chrome.gutter-user": "bold $teal",
            "chrome.note": "$iris",
            "chrome.elision": "$muted",
            "chrome.reasoning": "italic $muted",
            "chrome.reasoning-trace": "$muted",
            "chrome.heartbeat": "$muted",
            "chrome.rule": "$muted",
            "chrome.result": "$muted",
            "tool.icon": "$amber",
            "tool.name": "bold $amber",
            "tool.args": "$muted",
            "tool.ok": "$leaf",
            "tool.error": "$rose",
            "tool.card": "$muted",
            "markdown.code": "$teal",
            "markdown.link": "underline $sky",
            "markdown.quote": "italic $muted",
            "markdown.rule": "$muted",
            "syntax.keyword": "$iris",
            "syntax.string": "$leaf",
            "syntax.number": "$teal",
            "syntax.comment": "$muted",
            "syntax.function": "$sky",
            "syntax.type": "$amber",
            "diff.add": "$leaf",
            "diff.remove": "$rose",
            "diff.add-word": "reverse $leaf",
            "diff.remove-word": "reverse $rose",
            "diff.hunk": "$sky",
            "diff.filename": "bold $teal",
        },
    ),
    "mono": Theme(
        name="mono",
        description="no color — structure carried by bold/dim/reverse only",
        slots={
            slot.slot_id: {
                "base.selection": "reverse",
                "chrome.gutter-user": "bold",
                "chrome.user-text": "bold",
                "chrome.reasoning": "dim italic",
                "markdown.emphasis": "italic",
                "markdown.strong": "bold",
                "markdown.h1": "bold",
                "markdown.h2": "bold",
                "markdown.h3": "bold",
                "diff.add": "bold",
                "diff.remove": "dim",
                "diff.add-word": "reverse",
                "diff.remove-word": "reverse",
                "diff.meta": "bold",
                "tool.name": "bold",
                "status.error": "bold",
            }.get(slot.slot_id, "dim" if slot.family == "chrome" else "")
            for slot in SLOTS
        },
    ),
}

#: The theme used when ``[tui] theme`` is unset.
DEFAULT_THEME = "default"


# --------------------------------------------------------------------------
# Detection cascades (R-THEME-2 / R-THEME-4)
# --------------------------------------------------------------------------
def luminance(color: str) -> float | None:
    """Relative luminance (0..1) of a ``#rrggbb`` color, else ``None``.

    Args:
        color: A hex color (``#11141b``, with or without the ``#``).

    Returns:
        The perceptual luminance, or ``None`` when *color* is not hex.
    """
    text = color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        r, g, b = (int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


#: ``COLORFGBG`` background indices that mean a light terminal (7 = white,
#: 9-15 = bright colors). Everything else (0-6, 8) reads as dark.
_LIGHT_BG_INDICES = frozenset({"7", "9", "10", "11", "12", "13", "14", "15"})


def detect_mode(env: Mapping[str, str] | None = None) -> str:
    """Detect dark/light mode from the environment (R-THEME-2 cascade).

    Order, first hit wins:

    1. ``$CHIMERA_THEME_MODE`` (``dark``/``light``) — the explicit escape hatch.
    2. ``$CHIMERA_TERM_BG`` — a hex terminal background (what an OSC-11 query
       reports); luminance above 0.5 is light.
    3. ``$COLORFGBG`` — the widely-set ``fg;bg`` palette-index hint.
    4. Dark (the safe default: light text on dark is legible either way).

    Args:
        env: Environment mapping (defaults to :data:`os.environ`).

    Returns:
        ``"dark"`` or ``"light"``.
    """
    source = os.environ if env is None else env
    explicit = str(source.get("CHIMERA_THEME_MODE", "")).strip().lower()
    if explicit in ("dark", "light"):
        return explicit
    lum = luminance(str(source.get("CHIMERA_TERM_BG", "")))
    if lum is not None:
        return "light" if lum > 0.5 else "dark"
    fgbg = str(source.get("COLORFGBG", "")).strip()
    if fgbg:
        parts = [p for p in fgbg.split(";") if p != ""]
        if parts and parts[-1] in _LIGHT_BG_INDICES:
            return "light"
        if parts:
            return "dark"
    return "dark"


def color_depth(env: Mapping[str, str] | None = None) -> str:
    """Detect the terminal's usable color depth (R-THEME-4).

    Args:
        env: Environment mapping (defaults to :data:`os.environ`).

    Returns:
        ``"none"`` (``NO_COLOR`` set, or ``TERM=dumb``), ``"truecolor"``
        (``COLORTERM`` says so), ``"256"`` (``TERM`` mentions 256), else
        ``"16"``.
    """
    source = os.environ if env is None else env
    if str(source.get("NO_COLOR", "")) != "":
        return "none"
    term = str(source.get("TERM", "")).lower()
    if term == "dumb":
        return "none"
    colorterm = str(source.get("COLORTERM", "")).lower()
    if colorterm in ("truecolor", "24bit") or "direct" in term:
        return "truecolor"
    if "256" in term:
        return "256"
    return "16"


# xterm's 6×6×6 color-cube levels and the 24-step gray ramp.
_CUBE_LEVELS = (0, 95, 135, 175, 215, 255)
_GRAY_LEVELS = tuple(8 + 10 * i for i in range(24))
# The standard xterm base-16 RGB values, in rich's color-name order.
_ANSI16: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("black", (0, 0, 0)),
    ("red", (170, 0, 0)),
    ("green", (0, 170, 0)),
    ("yellow", (170, 85, 0)),
    ("blue", (0, 0, 170)),
    ("magenta", (170, 0, 170)),
    ("cyan", (0, 170, 170)),
    ("white", (170, 170, 170)),
    ("bright_black", (85, 85, 85)),
    ("bright_red", (255, 85, 85)),
    ("bright_green", (85, 255, 85)),
    ("bright_yellow", (255, 255, 85)),
    ("bright_blue", (85, 85, 255)),
    ("bright_magenta", (255, 85, 255)),
    ("bright_cyan", (85, 255, 255)),
    ("bright_white", (255, 255, 255)),
)
#: Style attributes that survive at every depth (they carry structure, not hue).
_ATTRIBUTES = frozenset({
    "bold", "dim", "italic", "underline", "blink", "reverse", "strike",
    "underline2", "frame", "encircle", "overline", "not",
})


def _rgb(color: str) -> tuple[int, int, int] | None:
    text = color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


def _nearest_256(rgb: tuple[int, int, int]) -> int:
    """Nearest xterm-256 palette index for an RGB triple."""
    r, g, b = rgb
    best_index, best_distance = 16, 1 << 30
    for ri, rv in enumerate(_CUBE_LEVELS):
        for gi, gv in enumerate(_CUBE_LEVELS):
            for bi, bv in enumerate(_CUBE_LEVELS):
                distance = (r - rv) ** 2 + (g - gv) ** 2 + (b - bv) ** 2
                if distance < best_distance:
                    best_distance = distance
                    best_index = 16 + 36 * ri + 6 * gi + bi
    for i, level in enumerate(_GRAY_LEVELS):
        distance = (r - level) ** 2 + (g - level) ** 2 + (b - level) ** 2
        if distance < best_distance:
            best_distance = distance
            best_index = 232 + i
    return best_index


def _nearest_16(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return min(
        _ANSI16,
        key=lambda entry: (r - entry[1][0]) ** 2
        + (g - entry[1][1]) ** 2
        + (b - entry[1][2]) ** 2,
    )[0]


def quantize(style: str, depth: str) -> str:
    """Degrade a style string to the terminal's color depth (R-THEME-4).

    Attributes (``bold``, ``dim``, ``reverse``, …) always survive — they carry
    structure, not hue — so ``NO_COLOR`` still reads as a designed interface
    rather than a flat wall of text. Named ANSI colors pass through untouched
    at every depth above ``none`` (they *are* the user's palette).

    Args:
        style: A rich style string (``"bold #8fd67a"``, ``"reverse red"``,
            ``"dim on #202020"``).
        depth: ``truecolor`` / ``256`` / ``16`` / ``none``.

    Returns:
        The style string for that depth (possibly empty).
    """
    tokens = style.split()
    out: list[str] = []
    for token in tokens:
        low = token.lower()
        if low in _ATTRIBUTES or low == "on":
            out.append(low)
            continue
        rgb = _rgb(token)
        if depth == "none":
            continue  # drop every color, keep attributes
        if rgb is None:
            out.append(token)  # a palette name: already depth-safe
            continue
        if depth == "truecolor":
            out.append(token if token.startswith("#") else f"#{token}")
        elif depth == "256":
            out.append(f"color({_nearest_256(rgb)})")
        else:
            out.append(_nearest_16(rgb))
    while out and out[-1] == "on":  # a dangling "on" with its color dropped
        out.pop()
    return " ".join(out)


# --------------------------------------------------------------------------
# Resolved palette
# --------------------------------------------------------------------------
#: Semantic slot → Textual design token. Only hex-valued slots are exported as
#: CSS variables (Textual's color system needs real colors), so a
#: terminal-palette theme leaves the framework chrome exactly as shipped.
_CSS_TOKENS: tuple[tuple[str, str], ...] = (
    ("base.primary", "primary"),
    ("base.accent", "accent"),
    ("base.border", "secondary"),
    ("base.background", "background"),
    ("base.surface", "surface"),
    ("base.panel", "panel"),
    ("base.text", "foreground"),
    ("status.error", "error"),
    ("status.warning", "warning"),
    ("status.success", "success"),
)


class Palette:
    """A resolved theme: slot → depth-quantized style string.

    This is what renderers hold. :meth:`style` never raises on an unknown slot
    (a typo must not kill a turn) — it returns the empty style, which renders
    as the terminal's default foreground.

    Args:
        theme: The resolved theme.
        mode: ``dark`` or ``light``.
        depth: Color depth from :func:`color_depth`.
    """

    def __init__(self, theme: Theme | None = None, *, mode: str = "dark",
                 depth: str = "truecolor") -> None:
        self.theme = theme if theme is not None else BUILTIN_THEMES[DEFAULT_THEME]
        self.mode = mode
        self.depth = depth
        self._raw = self.theme.resolve(mode)
        self._styles = {k: quantize(v, depth) for k, v in self._raw.items()}
        self._opacity = self.theme.opacities()

    @property
    def name(self) -> str:
        """The theme's name."""
        return self.theme.name

    def style(self, slot: str, *, extra: str = "") -> str:
        """The style string for *slot*, optionally with *extra* attributes.

        Args:
            slot: A slot id (``tool.name``); unknown ids yield ``""``.
            extra: Extra style tokens appended (``"bold"``).

        Returns:
            A rich-compatible style string, possibly empty.
        """
        base = self._styles.get(slot, "")
        if extra:
            return f"{base} {extra}".strip()
        return base

    def raw(self, slot: str) -> str:
        """The *unquantized* slot value (what the theme declared)."""
        return self._raw.get(slot, "")

    def opacity(self, knob: str) -> float:
        """An opacity knob's value (1.0 for unknown knobs)."""
        return self._opacity.get(knob, 1.0)

    def dim_for(self, knob: str) -> str:
        """``"dim"`` when an opacity knob calls for dimming, else ``""``.

        Terminals have no alpha channel; below :data:`_DIM_BELOW` the knob
        degrades to the ``dim`` attribute (R-THEME-4 honest degradation).
        """
        return "dim" if self.opacity(knob) < _DIM_BELOW else ""

    def css_variables(self) -> dict[str, str]:
        """Textual design-token overrides derived from the slots (R-THEME-3).

        Only hex slot values are exported — Textual's color system computes
        shades, which needs real colors, and a terminal-palette theme should
        leave framework chrome untouched anyway.

        Returns:
            ``token -> "#rrggbb"``; empty for palette-only themes.
        """
        out: dict[str, str] = {}
        if self.depth == "none":
            return out
        for slot, token in _CSS_TOKENS:
            value = self._raw.get(slot, "")
            for part in value.split():
                if _rgb(part) is not None and part.startswith("#"):
                    out[token] = part
                    break
        return out


# --------------------------------------------------------------------------
# User theme files + settings
# --------------------------------------------------------------------------
#: Theme-file extensions, in the order a scope is scanned.
_THEME_SUFFIXES = (".toml", ".json", ".yaml", ".yml")


def _read_theme_file(path: Path) -> dict[str, Any]:
    """Parse one theme file; ``{}`` on any failure (never blocks startup)."""
    try:
        if path.suffix == ".toml":
            import tomllib

            with path.open("rb") as handle:
                data: Any = tomllib.load(handle)
        else:
            from chimera.config.config_file import ChimeraConfig

            data = ChimeraConfig.from_file(path).data
    except Exception:  # noqa: BLE001 — a broken theme file must never crash
        return {}
    return data if isinstance(data, dict) else {}


def discover_themes(
    scopes: Iterable[str | os.PathLike[str]] | None = None,
) -> dict[str, Theme]:
    """Load built-in themes plus user theme files (R-THEME-3).

    Each scope contributes ``<scope>/themes/*.{toml,json,yaml,yml}``; the file
    stem is the theme name, and later scopes override earlier ones
    (defaults < XDG < user < project). Malformed files are skipped silently —
    a stale theme must never block a launch.

    Args:
        scopes: Config scope directories in ascending precedence order
            (typically :func:`chimera.config.user_config.tui_config_scopes`).
            ``None`` returns just the built-ins.

    Returns:
        ``name -> Theme``, built-ins first.
    """
    themes: dict[str, Theme] = dict(BUILTIN_THEMES)
    for scope in scopes or ():
        directory = Path(scope) / "themes"
        if not directory.is_dir():
            continue
        try:
            entries = sorted(directory.iterdir())
        except OSError:  # pragma: no cover - unreadable directory
            continue
        for path in entries:
            if path.suffix.lower() not in _THEME_SUFFIXES or not path.is_file():
                continue
            data = _read_theme_file(path)
            if not data:
                continue
            try:
                theme = Theme.from_dict(data, name=path.stem, source=str(path))
            except ThemeError:
                continue
            themes[theme.name] = theme
    return themes


@dataclass(frozen=True)
class ThemeSettings:
    """Resolved ``[tui]`` theme configuration (R-THEME-2/3/4).

    Args:
        theme: The theme name in effect.
        mode: The resolved mode (``dark``/``light``).
        mode_setting: What the config asked for (``auto``/``dark``/``light``/
            ``lock``) — ``lock`` means "detect once, ignore later terminal
            mode-change notifications".
        depth: Color depth from :func:`color_depth`.
        animations: Whether spinners/heartbeats animate (R-THEME-4).
        themes: The theme catalog these settings were resolved against.
        error: A human-readable problem with the config (unknown theme name,
            malformed theme), surfaced by the frontend; settings still resolve
            to a working default.
    """

    theme: str = DEFAULT_THEME
    mode: str = "dark"
    mode_setting: str = "auto"
    depth: str = "truecolor"
    animations: bool = True
    themes: Mapping[str, Theme] = field(default_factory=dict)
    error: str = ""

    @classmethod
    def resolve(
        cls,
        config: Mapping[str, Any] | None = None,
        *,
        env: Mapping[str, str] | None = None,
        themes: Mapping[str, Theme] | None = None,
    ) -> ThemeSettings:
        """Resolve the theme settings from a ``tui`` config section.

        Args:
            config: The ``tui`` section (``theme``, ``theme_mode``,
                ``animations``). ``None``/empty gives the shipped defaults.
            env: Environment mapping for the detection cascades.
            themes: Theme catalog; defaults to the built-ins.

        Returns:
            The resolved settings. Never raises: an unknown theme name or a
            bad mode falls back to the default and is reported via
            :attr:`error`.
        """
        source = dict(config or {})
        catalog = dict(themes) if themes is not None else dict(BUILTIN_THEMES)
        errors: list[str] = []

        name = str(source.get("theme", DEFAULT_THEME) or DEFAULT_THEME).strip()
        if name not in catalog:
            errors.append(
                f"unknown theme {name!r} — available: {', '.join(sorted(catalog))}"
            )
            name = DEFAULT_THEME

        mode_setting = str(source.get("theme_mode", "auto") or "auto").strip().lower()
        if mode_setting not in ("auto", "dark", "light", "lock"):
            errors.append(
                f"unknown theme_mode {mode_setting!r} — use auto, dark, light, or lock"
            )
            mode_setting = "auto"
        mode = mode_setting if mode_setting in ("dark", "light") else detect_mode(env)

        depth = color_depth(env)
        animations = source.get("animations", True)
        animate = bool(animations) if isinstance(animations, bool) else True
        if depth == "none":
            animate = False  # NO_COLOR implies reduced motion (spec §11)
        return cls(
            theme=name,
            mode=mode,
            mode_setting=mode_setting,
            depth=depth,
            animations=animate,
            themes=catalog,
            error="; ".join(errors),
        )

    def palette(self, theme: str | None = None) -> Palette:
        """Build the :class:`Palette` for these settings.

        Args:
            theme: Override the theme name (used by the ``/theme`` picker's
                live preview); unknown names fall back to the default.

        Returns:
            The resolved palette.
        """
        name = theme or self.theme
        chosen = self.themes.get(name) or BUILTIN_THEMES.get(name)
        if chosen is None:
            chosen = BUILTIN_THEMES[DEFAULT_THEME]
        return Palette(chosen, mode=self.mode, depth=self.depth)


def load_theme_settings(
    project_dir: str | os.PathLike[str] | None = None,
    *,
    home: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> ThemeSettings:
    """Read theme settings from the unified config chain (R-THEME-3).

    Reads the same XDG < user < project scopes the status line uses, and
    discovers user theme files under each scope's ``themes/`` directory.
    Config discovery is best-effort: a broken config or theme file degrades to
    the shipped defaults rather than blocking a launch.

    Args:
        project_dir: Project root (default: cwd).
        home: Home-directory override (tests).
        env: Environment mapping for the detection cascades.

    Returns:
        The resolved settings.
    """
    try:
        from chimera.config.user_config import load_tui_config, tui_config_scopes

        tui = load_tui_config(project_dir, home=home)
        catalog = discover_themes(tui_config_scopes(project_dir, home=home))
    except Exception:  # noqa: BLE001 — config discovery must not block a launch
        tui, catalog = {}, dict(BUILTIN_THEMES)
    return ThemeSettings.resolve(tui, env=env, themes=catalog)

"""Declarative keybinding registry for the Chimera TUIs (R-KEY-1..4).

One table — :data:`KEY_ACTIONS` — names every action the multiplexer binds and
drives, from the same data: the runtime ``BINDINGS`` (:func:`build_bindings`),
the footer hints (Textual renders them from the applied bindings), the
generated ``/help`` keys section and ``/keys`` table
(:func:`keymap_table`), and config-aware hint lookups (:func:`key_for`,
R-KEY-3) such as the tool-output elision marker.

User rebinding (R-KEY-2) reads a ``tui.keybinds`` table from the unified user
config chain (canonical ``~/.chimera/config.toml``, honoring
``$CHIMERA_CONFIG_HOME`` — see :mod:`chimera.config.user_config`)::

    [tui.keybinds]
    toggle_sidebar = "f2"                 # rebind
    toggle_reasoning = ["ctrl+e", "f3"]   # multiple keys
    clear_lane = false                    # unbind

:func:`resolve_keymap` validates overrides against the registry: unknown
actions raise a :class:`KeymapError` listing every valid id, two actions bound
to one key in the same context raise a loud conflict error naming both
(never silent shadowing), legacy action names migrate via
:data:`LEGACY_ACTION_NAMES`, and reserved actions (interrupt/quit) cannot be
unbound (R-KEY-4).

Contexts scope where a binding applies: ``global`` (the app), ``pager``
(full-screen pickers/overlays), plus ``composer`` and ``approval`` reserved
for the composer's editing keys and approval modals when those surfaces become
keymap-aware (later waves). The same key may serve different actions in
different contexts; conflicts are per-context.

This module is stdlib-only; the terminal framework is imported lazily inside
the two functions that build/mutate its binding objects, so the registry and
its validation stay importable (and unit-testable) everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from textual.binding import BindingsMap, BindingType  # type: ignore[import-not-found]

__all__ = [
    "KEY_ACTIONS",
    "LEGACY_ACTION_NAMES",
    "ActionDef",
    "KeymapError",
    "ResolvedBinding",
    "apply_keymap",
    "build_bindings",
    "display_key",
    "hidden_actions",
    "key_for",
    "keymap_table",
    "load_user_keybinds",
    "resolve_keymap",
]


class KeymapError(ValueError):
    """A ``tui.keybinds`` override is invalid (unknown action, conflict, …)."""


@dataclass(frozen=True)
class ActionDef:
    """One named action in the keybinding registry (R-KEY-1).

    Args:
        action_id: The action name, matching the ``action_<id>`` handler on
            the app/screen that binds it.
        default_keys: Default key(s), in framework key syntax
            (``ctrl+x``, ``shift+tab``, ``escape``, ``f2``).
        description: Footer/help label.
        context: Where the binding applies: ``global`` (app-wide),
            ``pager`` (full-screen pickers), ``composer`` / ``approval``
            (reserved for later waves).
        show_in_footer: Whether the binding is advertised in the footer.
        reserved: Reserved actions (interrupt/quit) cannot be unbound
            (R-KEY-4). They may still be rebound to different keys.
        priority: Framework priority binding — checked app-first so the
            focused composer widget cannot swallow it.
        single_only: Live only in single-lane mode (hidden with 2+ lanes).
        multi_only: Live only in multi-lane mode (hidden with one lane).
    """

    action_id: str
    default_keys: tuple[str, ...]
    description: str
    context: str = "global"
    show_in_footer: bool = True
    reserved: bool = False
    priority: bool = False
    single_only: bool = False
    multi_only: bool = False


#: The one table (R-KEY-1). Order matters: it is the footer / ``/keys`` order,
#: and it mirrors the multiplexer's historical ``BINDINGS`` list exactly, plus
#: the R-FOLD-2 expand toggle and the cohort picker's pager keys.
KEY_ACTIONS: tuple[ActionDef, ...] = (
    ActionDef("cancel_all", ("ctrl+c",), "Cancel all / quit",
              priority=True, reserved=True),
    ActionDef("quit", ("ctrl+d",), "Quit", reserved=True),
    # Tab completes a "/" command when one is being typed, else cycles focus.
    ActionDef("smart_tab", ("tab",), "Complete / focus →", priority=True),
    ActionDef("focus_prev_lane", ("shift+tab",), "Focus ←",
              priority=True, multi_only=True),
    ActionDef("toggle_broadcast", ("ctrl+b",), "Broadcast/target", multi_only=True),
    ActionDef("cancel_focused", ("ctrl+g",), "Cancel lane", multi_only=True),
    ActionDef("clear_focused", ("ctrl+o",), "Clear lane", multi_only=True),
    # Single-lane alias (check_action swaps which of the two clears is live).
    ActionDef("clear_lane", ("ctrl+l",), "Clear", single_only=True),
    ActionDef("show_results", ("ctrl+r",), "Compare results"),
    # priority: the prompt's editor binds ctrl+e (cursor to line end) and
    # would otherwise swallow the advertised reasoning toggle.
    ActionDef("toggle_reasoning", ("ctrl+e",), "Reasoning", priority=True),
    ActionDef("toggle_sidebar", ("ctrl+t",), "Sidebar"),
    # R-FOLD-2 global expand toggle. priority: shadows the editor's ctrl+x
    # (cut) while the composer is focused — the expand affordance is
    # advertised on every elision marker, so it must always fire.
    ActionDef("toggle_expand", ("ctrl+x",), "Expand tool output", priority=True),
    # Copy the current transcript selection to the system clipboard (OSC 52).
    # Ctrl+C stays the cancel key (universal muscle memory); copy is Ctrl+Y —
    # priority so it fires even while the composer is focused, since a selection
    # is screen-level and persists across focus.
    ActionDef("copy_selection", ("ctrl+y",), "Copy selection", priority=True),
    # Pager context: the cohort picker's dismiss keys ("q" is safe here —
    # no text input has focus on a pager screen).
    ActionDef("close", ("escape", "q"), "Back", context="pager"),
)

#: Renamed actions (R-KEY-2 migration): configs written against the retired
#: single-agent app's action names keep working. old name → current name.
LEGACY_ACTION_NAMES: dict[str, str] = {
    "cancel": "cancel_all",       # the retired app's Ctrl+C action
    "clear_convo": "clear_lane",  # the retired app's Ctrl+L action
}

_BY_ID: dict[str, ActionDef] = {a.action_id: a for a in KEY_ACTIONS}


@dataclass(frozen=True)
class ResolvedBinding:
    """An action's effective keys after applying user overrides.

    Args:
        action: The registry entry.
        keys: The keys now bound to it (empty when unbound).
        source: Where the keys came from: ``default``, ``user`` (a
            ``tui.keybinds`` override), or ``migrated`` (an override that
            arrived under a legacy action name).
    """

    action: ActionDef
    keys: tuple[str, ...]
    source: str = "default"


def _normalize_key(key: object, action_id: str) -> str:
    """Normalize one key string from config; reject non-keys loudly."""
    if not isinstance(key, str):
        raise KeymapError(
            f"tui.keybinds[{action_id!r}]: key must be a string, a list of "
            f"strings, or false to unbind (got {key!r})"
        )
    cleaned = key.strip()
    if not cleaned or any(ch.isspace() for ch in cleaned):
        raise KeymapError(f"tui.keybinds[{action_id!r}]: invalid key {key!r}")
    # Lowercase multi-char names/modifiers ("Ctrl+X" → "ctrl+x"); a bare
    # single character keeps its case (shifted letters are case-significant).
    return cleaned if len(cleaned) == 1 else cleaned.lower()


def _override_keys(value: object, action_id: str) -> tuple[str, ...] | None:
    """Interpret one override value: key, list of keys, or ``false``/empty.

    Returns:
        The key tuple, or ``None`` for an unbind request.
    """
    if value is False or value is None:
        return None
    if value is True:
        raise KeymapError(
            f"tui.keybinds[{action_id!r}]: 'true' is not a key — give a key "
            "string, a list of keys, or false to unbind"
        )
    if isinstance(value, str):
        return (_normalize_key(value, action_id),)
    if isinstance(value, (list, tuple)):
        keys = tuple(_normalize_key(k, action_id) for k in value)
        return keys or None  # an empty list is an unbind request
    raise KeymapError(
        f"tui.keybinds[{action_id!r}]: expected a key string, a list of "
        f"strings, or false to unbind (got {value!r})"
    )


def resolve_keymap(
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, ResolvedBinding]:
    """Merge user overrides over the registry defaults, validating loudly.

    Args:
        overrides: A ``tui.keybinds``-shaped table: action name → key string,
            list of keys, or ``false`` to unbind. ``None``/empty gives the
            pure default keymap.

    Returns:
        action_id → :class:`ResolvedBinding`, one entry per registry action
        (unbound actions carry an empty key tuple).

    Raises:
        KeymapError: On an unknown action name (message lists every valid
            id), an attempt to unbind a reserved action (R-KEY-4), a
            malformed key value, or two actions resolving to the same key in
            the same context (both are named — never silent shadowing).
    """
    resolved: dict[str, ResolvedBinding] = {
        a.action_id: ResolvedBinding(a, a.default_keys) for a in KEY_ACTIONS
    }
    for raw_name, value in (overrides or {}).items():
        name = str(raw_name)
        source = "user"
        if name in LEGACY_ACTION_NAMES:
            name = LEGACY_ACTION_NAMES[name]
            source = "migrated"
        action = _BY_ID.get(name)
        if action is None:
            valid = ", ".join(sorted(_BY_ID))
            raise KeymapError(
                f"tui.keybinds: unknown action {raw_name!r} — valid actions: {valid}"
            )
        keys = _override_keys(value, name)
        if keys is None:
            if action.reserved:
                raise KeymapError(
                    f"tui.keybinds: {name!r} is reserved (interrupt/quit) and "
                    "cannot be unbound — rebind it to a different key instead"
                )
            keys = ()
        # dedupe while preserving order (a repeated key is harmless intent)
        keys = tuple(dict.fromkeys(keys))
        resolved[name] = ResolvedBinding(action, keys, source)

    seen: dict[tuple[str, str], str] = {}
    for rb in resolved.values():
        for key in rb.keys:
            slot = (rb.action.context, key)
            other = seen.get(slot)
            if other is not None:
                raise KeymapError(
                    f"tui.keybinds: key {key!r} is bound to both {other!r} and "
                    f"{rb.action.action_id!r} in the {rb.action.context!r} "
                    "context — rebind or unbind one of them"
                )
            seen[slot] = rb.action.action_id
    return resolved


def key_for(
    action_id: str, keymap: Mapping[str, ResolvedBinding] | None = None,
) -> str:
    """The currently-bound key for an action, for on-screen hints (R-KEY-3).

    Args:
        action_id: A registry action id.
        keymap: A resolved keymap; ``None`` reads the defaults.

    Returns:
        The action's first bound key (``"ctrl+x"``), or ``""`` when the user
        unbound it — hint call sites must then drop the hint, not lie.

    Raises:
        KeymapError: If *action_id* is not in the registry.
    """
    if action_id not in _BY_ID:
        raise KeymapError(f"unknown action {action_id!r}")
    if keymap is not None and action_id in keymap:
        keys = keymap[action_id].keys
    else:
        keys = _BY_ID[action_id].default_keys
    return keys[0] if keys else ""


_KEY_DISPLAY = {"escape": "Esc"}


def display_key(key: str) -> str:
    """Human form of a key string: ``ctrl+x`` → ``Ctrl+X``, ``escape`` → ``Esc``."""
    return "+".join(
        _KEY_DISPLAY.get(part, part.upper() if len(part) == 1 else part.capitalize())
        for part in key.split("+")
    )


def hidden_actions(single: bool) -> frozenset[str]:
    """Actions disabled for a lane mode — drives the app's ``check_action``.

    Single-lane mode hides the multi-lane chrome actions (focus cycling,
    broadcast toggle, per-lane cancel/clear); multi-lane mode hides the
    single-lane aliases. This is the #172 gating, registry-owned.

    Args:
        single: True for the one-lane (daily-driver) surface.

    Returns:
        The action ids ``check_action`` should refuse.
    """
    if single:
        return frozenset(a.action_id for a in KEY_ACTIONS if a.multi_only)
    return frozenset(a.action_id for a in KEY_ACTIONS if a.single_only)


def keymap_table(keymap: Mapping[str, ResolvedBinding] | None = None) -> list[str]:
    """The effective binding table, one row per action (drives ``/keys``).

    Args:
        keymap: A resolved keymap; ``None`` shows the defaults.

    Returns:
        Aligned text rows: action, keys (or ``(unbound)``), context, and the
        source of the binding (``default`` / ``user`` / ``migrated``, with a
        ``reserved`` marker where R-KEY-4 applies).
    """
    if keymap is None:
        keymap = resolve_keymap({})
    rows: list[tuple[str, str, str, str]] = []
    for action in KEY_ACTIONS:
        rb = keymap.get(action.action_id, ResolvedBinding(action, action.default_keys))
        keys = " ".join(rb.keys) if rb.keys else "(unbound)"
        source = rb.source + (" · reserved" if action.reserved else "")
        rows.append((action.action_id, keys, action.context, source))
    id_w = max(len(r[0]) for r in rows)
    key_w = max(len(r[1]) for r in rows)
    ctx_w = max(len(r[2]) for r in rows)
    return [
        f"{aid:<{id_w}}  {keys:<{key_w}}  {ctx:<{ctx_w}}  {source}"
        for aid, keys, ctx, source in rows
    ]


def load_user_keybinds() -> dict[str, Any]:
    """Read the ``tui.keybinds`` override table from the user config chain.

    Reads the user scope through the unified config loader
    (:func:`chimera.config.user_config.load_user_scope_config`): the canonical
    ``~/.chimera/config.toml`` (honoring ``$CHIMERA_CONFIG_HOME``), now also
    accepting a ``config.{yaml,yml,json}`` in the same directory. A missing
    file, missing table, or malformed section reads as "no overrides" — startup
    must never fail on config discovery. (Validation of the table's *contents*
    is :func:`resolve_keymap`'s job, and is loud.)

    Returns:
        The raw override table (possibly empty).
    """
    from chimera.config.user_config import load_user_scope_config

    tui = load_user_scope_config().get("tui")
    if not isinstance(tui, dict):
        return {}
    keybinds = tui.get("keybinds")
    return dict(keybinds) if isinstance(keybinds, dict) else {}


def build_bindings(
    keymap: Mapping[str, ResolvedBinding] | None = None,
    *,
    context: str = "global",
) -> list[BindingType]:
    """Build the framework ``Binding`` list for one context from the registry.

    Args:
        keymap: A resolved keymap; ``None`` builds the defaults (this is what
            class-level ``BINDINGS`` tables use).
        context: Which context's actions to emit (``global``, ``pager``).

    Returns:
        One ``Binding`` per bound key, in registry order — footer order is
        registry order. (Typed as the framework's ``BindingType`` union so
        the list assigns directly to a class ``BINDINGS`` attribute.)
    """
    from textual.binding import Binding  # type: ignore[import-not-found]

    bindings: list[BindingType] = []
    for action in KEY_ACTIONS:
        if action.context != context:
            continue
        rb = (
            keymap.get(action.action_id) if keymap is not None else None
        ) or ResolvedBinding(action, action.default_keys)
        for key in rb.keys:
            bindings.append(Binding(
                key,
                action.action_id,
                action.description,
                show=action.show_in_footer,
                priority=action.priority,
            ))
    return bindings


def apply_keymap(
    bindings_map: BindingsMap,
    keymap: Mapping[str, ResolvedBinding],
    *,
    context: str = "global",
) -> None:
    """Apply user overrides to a live widget's bindings, in place (R-KEY-2).

    The framework snapshots class-level ``BINDINGS`` into a per-instance map
    at construction; this rewrites that instance map so every overridden
    registry action is bound to exactly its resolved keys — stale default
    keys removed, new keys added (keeping the action's declared priority and
    footer visibility). Framework-internal bindings (command palette, etc.)
    and non-overridden actions are untouched. Footer hints follow
    automatically, so they stay true after rebinding (R-KEY-3).

    A user key that collides with a framework-internal binding is not
    detected here — conflict validation covers registry actions only.

    Args:
        bindings_map: The instance's ``_bindings`` map (a framework
            ``BindingsMap``).
        keymap: A resolved keymap from :func:`resolve_keymap`.
        context: Which context's actions this widget owns.
    """
    changed = {
        rb.action.action_id: rb
        for rb in keymap.values()
        if rb.source != "default" and rb.action.context == context
    }
    if not changed:
        return
    key_to_bindings = bindings_map.key_to_bindings
    for key in list(key_to_bindings):
        kept = [
            b for b in key_to_bindings[key]
            if b.action not in changed or key in changed[b.action].keys
        ]
        if kept:
            key_to_bindings[key] = kept
        else:
            del key_to_bindings[key]
    for action_id, rb in changed.items():
        action = rb.action
        for key in rb.keys:
            if any(b.action == action_id for b in key_to_bindings.get(key, [])):
                continue  # already bound (a kept default)
            bindings_map.bind(
                key,
                action_id,
                description=action.description,
                show=action.show_in_footer,
                priority=action.priority,
            )

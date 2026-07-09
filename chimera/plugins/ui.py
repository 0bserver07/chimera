"""Third-party UI-surface registration for the REPL and TUI.

Plugins can already contribute tools, loops, providers, agents, strategies,
constraints, middleware, skills, MCP servers, and hooks (see
:mod:`chimera.plugins.base` and :mod:`chimera.plugins.registry`). What they
could not do is extend the *interactive surface* — the slash commands, side
panels, and status-line segments a user sees in ``chimera code`` (REPL) or the
TUI — without editing core.

This module closes that gap with a small, typed contribution model:

* :class:`UICommand` — a slash command (name, handler, help, aliases).
* :class:`UIPanel` — a panel/pane the TUI can render (id, title, renderer,
  placement, order).
* :class:`UIStatusline` — a status-line segment (id, renderer, section, order).

and a process-global :class:`UIExtensionRegistry` that aggregates them. The
registry mirrors :class:`chimera.plugins.registry.PluginExtensionRegistry`:
class-level state, classmethod registration, ``get_all_*`` accessors, and a
``_reset`` hook for tests.

Two registration styles are supported. The imperative style::

    from chimera.plugins.ui import UIExtensionRegistry

    def greet(session, env, args, out):
        out("hello from a plugin")

    UIExtensionRegistry.register_command("greet", greet, help="say hello")

and the decorator (``on``-style) style, which registers the callable and
returns it unchanged so it stays directly usable::

    @UIExtensionRegistry.on_command("greet", help="say hello")
    def greet(session, env, args, out):
        out("hello from a plugin")

    @UIExtensionRegistry.on_panel("files", title="Changed files")
    def render_files(ctx):
        return ["a.py", "b.py"]

    @UIExtensionRegistry.on_statusline("cost", section="right")
    def render_cost(ctx):
        return "$0.01"

Enumeration accessors (:meth:`~UIExtensionRegistry.get_all_commands`,
:meth:`~UIExtensionRegistry.get_all_panels`,
:meth:`~UIExtensionRegistry.get_all_statuslines`) are what a REPL/TUI front-end
calls to discover contributions. :func:`install_into_repl` is the concrete
bridge that surfaces every registered :class:`UICommand` into the live
``chimera code`` dispatch registry so a plugin command becomes a working
``/name`` at the prompt.

The module is stdlib-only. A :class:`UICommand` handler follows the REPL's
command-handler contract — ``(session, env, args, out) -> None`` — so the same
callable works whether it is dispatched by the REPL or invoked directly.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "PanelPlacement",
    "StatuslineSection",
    "UICommand",
    "UIPanel",
    "UIStatusline",
    "UIExtensionRegistry",
    "install_into_repl",
]


# ---------------------------------------------------------------------------
# Placement vocabularies
# ---------------------------------------------------------------------------

class PanelPlacement(str, Enum):
    """Where a :class:`UIPanel` asks to be rendered.

    Inherits from :class:`str` so a member compares equal to its value
    (``PanelPlacement.SIDEBAR == "sidebar"``). A front-end may accept a raw
    string for placements it understands beyond these well-known values;
    the enum documents the vocabulary Chimera front-ends recognise today.
    """

    SIDEBAR = "sidebar"
    BOTTOM = "bottom"
    OVERLAY = "overlay"


class StatuslineSection(str, Enum):
    """Which part of the status line a :class:`UIStatusline` segment targets.

    Inherits from :class:`str` so a member compares equal to its value
    (``StatuslineSection.RIGHT == "right"``).
    """

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


# ---------------------------------------------------------------------------
# Contribution dataclasses
# ---------------------------------------------------------------------------

# A REPL command handler: ``(session, env, args, out) -> None``. Kept as a
# permissive alias (``Callable[..., Any]``) so front-ends with a different
# arity can reuse the model without a type error at the call site.
CommandHandler = Callable[..., Any]

# A panel/status renderer receives a single opaque context object supplied by
# the front-end (the live session, a view model, etc.) and returns whatever the
# front-end knows how to draw (a string, a list of lines, a widget, ...).
Renderer = Callable[..., Any]


@dataclass(frozen=True)
class UICommand:
    """A slash command contributed by a plugin.

    Args:
        name: Command name without the leading slash (e.g. ``"greet"``).
        handler: Callable following the REPL command-handler contract,
            ``(session, env, args, out) -> None``.
        help: One-line description shown by ``/help`` and completion.
        aliases: Alternative names that resolve to the same handler.
        plugin: Name of the contributing plugin, for provenance. ``None``
            when registered outside a plugin (e.g. in a test).
    """

    name: str
    handler: CommandHandler
    help: str = ""
    aliases: tuple[str, ...] = ()
    plugin: str | None = None


@dataclass(frozen=True)
class UIPanel:
    """A panel/pane contributed by a plugin for the TUI to render.

    Args:
        id: Stable identifier, unique within the registry.
        renderer: Callable the front-end invokes to produce the panel's
            content. It receives a front-end-supplied context object and
            returns a front-end-renderable value.
        title: Human-readable title shown in the panel chrome.
        placement: Where the panel wants to appear. Accepts a
            :class:`PanelPlacement` or any string a front-end understands.
        order: Sort key among panels sharing a placement (ascending;
            lower renders first).
        plugin: Name of the contributing plugin, for provenance.
    """

    id: str
    renderer: Renderer
    title: str = ""
    placement: str = PanelPlacement.SIDEBAR.value
    order: int = 100
    plugin: str | None = None


@dataclass(frozen=True)
class UIStatusline:
    """A status-line segment contributed by a plugin.

    Args:
        id: Stable identifier, unique within the registry.
        renderer: Callable the front-end invokes to produce the segment's
            text. It receives a front-end-supplied context object and
            returns a short string (or other renderable value).
        section: Which part of the status line to render in. Accepts a
            :class:`StatuslineSection` or any string a front-end understands.
        order: Sort key within a section (ascending; lower renders first).
        plugin: Name of the contributing plugin, for provenance.
    """

    id: str
    renderer: Renderer
    section: str = StatuslineSection.RIGHT.value
    order: int = 100
    plugin: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_command_name(name: str) -> str:
    """Return a bare command name (no leading slash, whitespace-trimmed).

    Args:
        name: A user-supplied command name, with or without a leading ``/``.

    Returns:
        The normalized name.

    Raises:
        ValueError: If *name* is empty after normalization.
    """
    cleaned = name.strip()
    if cleaned.startswith("/"):
        cleaned = cleaned[1:].strip()
    if not cleaned:
        raise ValueError("command name must be a non-empty string")
    return cleaned


def _normalize_aliases(aliases: Iterable[str]) -> tuple[str, ...]:
    """Normalize an iterable of aliases, dropping blanks and duplicates.

    Args:
        aliases: Candidate alias names (with or without leading slashes).

    Returns:
        A de-duplicated tuple of normalized alias names, order preserved.
    """
    seen: dict[str, None] = {}
    for alias in aliases:
        cleaned = alias.strip()
        if cleaned.startswith("/"):
            cleaned = cleaned[1:].strip()
        if cleaned:
            seen.setdefault(cleaned, None)
    return tuple(seen)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class UIExtensionRegistry:
    """Process-global registry for plugin-provided UI surfaces.

    Aggregates slash commands, panels, and status-line segments contributed
    by plugins so a REPL or TUI front-end can enumerate them with a single
    call. State is class-level (matching
    :class:`chimera.plugins.registry.PluginExtensionRegistry`); use
    :meth:`_reset` between tests.

    Registration is idempotent-by-name: registering a command, panel, or
    status-line whose name/id already exists replaces the earlier entry
    (last write wins), which is what plugin hot-reload needs.
    """

    _commands: dict[str, UICommand] = {}
    _command_aliases: dict[str, str] = {}
    _panels: dict[str, UIPanel] = {}
    _statuslines: dict[str, UIStatusline] = {}

    # -- Commands -------------------------------------------------------------

    @classmethod
    def register_command(
        cls,
        name: str,
        handler: CommandHandler,
        *,
        help: str = "",
        aliases: Iterable[str] = (),
        plugin: str | None = None,
    ) -> UICommand:
        """Register a slash command.

        Args:
            name: Command name (a leading ``/`` is stripped if present).
            handler: Callable following the REPL command-handler contract,
                ``(session, env, args, out) -> None``.
            help: One-line description for ``/help`` and completion.
            aliases: Alternative names resolving to the same handler.
            plugin: Contributing plugin name, for provenance.

        Returns:
            The stored :class:`UICommand`.

        Raises:
            ValueError: If *name* is empty or *handler* is not callable.
        """
        cmd = UICommand(
            name=_normalize_command_name(name),
            handler=handler,
            help=help,
            aliases=_normalize_aliases(aliases),
            plugin=plugin,
        )
        return cls.add_command(cmd)

    @classmethod
    def add_command(cls, command: UICommand) -> UICommand:
        """Store a pre-built :class:`UICommand`.

        Args:
            command: The command to store.

        Returns:
            The stored command (same instance).

        Raises:
            ValueError: If the command's handler is not callable.
        """
        if not callable(command.handler):
            raise ValueError(f"handler for command '{command.name}' is not callable")
        # Drop any stale alias rows that pointed at a previous registration of
        # this name so a re-register cannot leave dangling aliases behind.
        cls._command_aliases = {
            alias: target
            for alias, target in cls._command_aliases.items()
            if target != command.name
        }
        cls._commands[command.name] = command
        for alias in command.aliases:
            cls._command_aliases[alias] = command.name
        return command

    @classmethod
    def on_command(
        cls,
        name: str,
        *,
        help: str = "",
        aliases: Iterable[str] = (),
        plugin: str | None = None,
    ) -> Callable[[CommandHandler], CommandHandler]:
        """Decorator form of :meth:`register_command`.

        Registers the decorated function as a slash command and returns it
        unchanged, so the callable stays usable directly.

        Args:
            name: Command name (a leading ``/`` is stripped if present).
            help: One-line description for ``/help`` and completion.
            aliases: Alternative names resolving to the same handler.
            plugin: Contributing plugin name, for provenance.

        Returns:
            A decorator that registers and returns the handler.
        """

        def decorator(handler: CommandHandler) -> CommandHandler:
            cls.register_command(
                name, handler, help=help, aliases=aliases, plugin=plugin
            )
            return handler

        return decorator

    @classmethod
    def get_command(cls, name: str) -> UICommand | None:
        """Look up a command by name or alias.

        Args:
            name: Command name or alias (a leading ``/`` is ignored).

        Returns:
            The matching :class:`UICommand`, or ``None`` if unknown.
        """
        try:
            key = _normalize_command_name(name)
        except ValueError:
            return None
        if key in cls._commands:
            return cls._commands[key]
        canonical = cls._command_aliases.get(key)
        if canonical is not None:
            return cls._commands.get(canonical)
        return None

    @classmethod
    def get_all_commands(cls) -> list[UICommand]:
        """Return every registered command, sorted by name.

        Returns:
            A list of :class:`UICommand`, ascending by ``name``.
        """
        return sorted(cls._commands.values(), key=lambda c: c.name)

    # -- Panels ---------------------------------------------------------------

    @classmethod
    def register_panel(
        cls,
        id: str,
        renderer: Renderer,
        *,
        title: str = "",
        placement: str = PanelPlacement.SIDEBAR.value,
        order: int = 100,
        plugin: str | None = None,
    ) -> UIPanel:
        """Register a panel/pane for the TUI to render.

        Args:
            id: Stable identifier, unique within the registry.
            renderer: Callable producing the panel's content.
            title: Human-readable title shown in the panel chrome.
            placement: Where the panel wants to appear (a
                :class:`PanelPlacement` or a string a front-end understands).
            order: Sort key among panels sharing a placement (ascending).
            plugin: Contributing plugin name, for provenance.

        Returns:
            The stored :class:`UIPanel`.

        Raises:
            ValueError: If *id* is empty or *renderer* is not callable.
        """
        panel = UIPanel(
            id=_require_id(id, "panel"),
            renderer=renderer,
            title=title,
            placement=_placement_value(placement),
            order=order,
            plugin=plugin,
        )
        return cls.add_panel(panel)

    @classmethod
    def add_panel(cls, panel: UIPanel) -> UIPanel:
        """Store a pre-built :class:`UIPanel`.

        Args:
            panel: The panel to store.

        Returns:
            The stored panel (same instance).

        Raises:
            ValueError: If the panel's renderer is not callable.
        """
        if not callable(panel.renderer):
            raise ValueError(f"renderer for panel '{panel.id}' is not callable")
        cls._panels[panel.id] = panel
        return panel

    @classmethod
    def on_panel(
        cls,
        id: str,
        *,
        title: str = "",
        placement: str = PanelPlacement.SIDEBAR.value,
        order: int = 100,
        plugin: str | None = None,
    ) -> Callable[[Renderer], Renderer]:
        """Decorator form of :meth:`register_panel`.

        Registers the decorated function as a panel renderer and returns it
        unchanged.

        Args:
            id: Stable identifier, unique within the registry.
            title: Human-readable title shown in the panel chrome.
            placement: Where the panel wants to appear.
            order: Sort key among panels sharing a placement (ascending).
            plugin: Contributing plugin name, for provenance.

        Returns:
            A decorator that registers and returns the renderer.
        """

        def decorator(renderer: Renderer) -> Renderer:
            cls.register_panel(
                id,
                renderer,
                title=title,
                placement=placement,
                order=order,
                plugin=plugin,
            )
            return renderer

        return decorator

    @classmethod
    def get_panel(cls, id: str) -> UIPanel | None:
        """Look up a panel by id.

        Args:
            id: The panel identifier.

        Returns:
            The matching :class:`UIPanel`, or ``None`` if unknown.
        """
        return cls._panels.get(id)

    @classmethod
    def get_all_panels(cls, placement: str | None = None) -> list[UIPanel]:
        """Return registered panels, sorted by ``(order, id)``.

        Args:
            placement: If given, return only panels whose placement matches
                (a :class:`PanelPlacement` or an equivalent string).

        Returns:
            A list of :class:`UIPanel`, ascending by ``order`` then ``id``.
        """
        panels = list(cls._panels.values())
        if placement is not None:
            wanted = _placement_value(placement)
            panels = [p for p in panels if p.placement == wanted]
        return sorted(panels, key=lambda p: (p.order, p.id))

    # -- Status line ----------------------------------------------------------

    @classmethod
    def register_statusline(
        cls,
        id: str,
        renderer: Renderer,
        *,
        section: str = StatuslineSection.RIGHT.value,
        order: int = 100,
        plugin: str | None = None,
    ) -> UIStatusline:
        """Register a status-line segment.

        Args:
            id: Stable identifier, unique within the registry.
            renderer: Callable producing the segment's text.
            section: Which part of the status line to render in (a
                :class:`StatuslineSection` or an equivalent string).
            order: Sort key within a section (ascending).
            plugin: Contributing plugin name, for provenance.

        Returns:
            The stored :class:`UIStatusline`.

        Raises:
            ValueError: If *id* is empty or *renderer* is not callable.
        """
        segment = UIStatusline(
            id=_require_id(id, "statusline"),
            renderer=renderer,
            section=_section_value(section),
            order=order,
            plugin=plugin,
        )
        return cls.add_statusline(segment)

    @classmethod
    def add_statusline(cls, statusline: UIStatusline) -> UIStatusline:
        """Store a pre-built :class:`UIStatusline`.

        Args:
            statusline: The segment to store.

        Returns:
            The stored segment (same instance).

        Raises:
            ValueError: If the segment's renderer is not callable.
        """
        if not callable(statusline.renderer):
            raise ValueError(
                f"renderer for statusline '{statusline.id}' is not callable"
            )
        cls._statuslines[statusline.id] = statusline
        return statusline

    @classmethod
    def on_statusline(
        cls,
        id: str,
        *,
        section: str = StatuslineSection.RIGHT.value,
        order: int = 100,
        plugin: str | None = None,
    ) -> Callable[[Renderer], Renderer]:
        """Decorator form of :meth:`register_statusline`.

        Registers the decorated function as a status-line renderer and
        returns it unchanged.

        Args:
            id: Stable identifier, unique within the registry.
            section: Which part of the status line to render in.
            order: Sort key within a section (ascending).
            plugin: Contributing plugin name, for provenance.

        Returns:
            A decorator that registers and returns the renderer.
        """

        def decorator(renderer: Renderer) -> Renderer:
            cls.register_statusline(
                id, renderer, section=section, order=order, plugin=plugin
            )
            return renderer

        return decorator

    @classmethod
    def get_statusline(cls, id: str) -> UIStatusline | None:
        """Look up a status-line segment by id.

        Args:
            id: The segment identifier.

        Returns:
            The matching :class:`UIStatusline`, or ``None`` if unknown.
        """
        return cls._statuslines.get(id)

    @classmethod
    def get_all_statuslines(cls, section: str | None = None) -> list[UIStatusline]:
        """Return registered status-line segments, sorted by ``(order, id)``.

        Args:
            section: If given, return only segments whose section matches
                (a :class:`StatuslineSection` or an equivalent string).

        Returns:
            A list of :class:`UIStatusline`, ascending by ``order`` then ``id``.
        """
        segments = list(cls._statuslines.values())
        if section is not None:
            wanted = _section_value(section)
            segments = [s for s in segments if s.section == wanted]
        return sorted(segments, key=lambda s: (s.order, s.id))

    # -- Reset (for testing) --------------------------------------------------

    @classmethod
    def _reset(cls) -> None:
        """Clear all UI registrations. Used in tests."""
        cls._commands.clear()
        cls._command_aliases.clear()
        cls._panels.clear()
        cls._statuslines.clear()


# ---------------------------------------------------------------------------
# Coercion helpers (module-level so both dataclasses and the registry share them)
# ---------------------------------------------------------------------------

def _require_id(value: str, kind: str) -> str:
    """Return a trimmed, non-empty identifier or raise.

    Args:
        value: The candidate identifier.
        kind: Human-readable surface name for the error message.

    Returns:
        The trimmed identifier.

    Raises:
        ValueError: If *value* is empty after trimming.
    """
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{kind} id must be a non-empty string")
    return cleaned


def _placement_value(placement: str) -> str:
    """Return the string value for a placement (enum member or raw string)."""
    if isinstance(placement, PanelPlacement):
        return placement.value
    return str(placement)


def _section_value(section: str) -> str:
    """Return the string value for a section (enum member or raw string)."""
    if isinstance(section, StatuslineSection):
        return section.value
    return str(section)


# ---------------------------------------------------------------------------
# REPL bridge
# ---------------------------------------------------------------------------

def install_into_repl(
    register: Callable[[str, Any, str], None] | None = None,
    *,
    registry: type[UIExtensionRegistry] = UIExtensionRegistry,
) -> list[str]:
    """Surface every registered :class:`UICommand` into the live REPL registry.

    This is the concrete bridge that makes a plugin-registered command a
    working ``/name`` at the ``chimera code`` prompt: it enumerates
    :meth:`UIExtensionRegistry.get_all_commands` and calls the REPL's
    ``register(name, handler, help_text)`` for each command and each of its
    aliases. After this call, the command appears in the REPL's
    ``list_commands()`` / ``COMMAND_NAMES`` and dispatches through the shared
    slash-command router.

    Args:
        register: The REPL registration function. Defaults to
            :func:`chimera.cli.slash_commands.register`. Injectable so callers
            (and tests) can target a different registry.
        registry: The UI registry to read from. Defaults to the global
            :class:`UIExtensionRegistry`; injectable for tests.

    Returns:
        The list of command names and aliases installed, in registration order.
    """
    if register is None:
        from chimera.cli.slash_commands import register as _repl_register

        do_register: Callable[..., Any] = _repl_register
    else:
        do_register = register

    installed: list[str] = []
    for cmd in registry.get_all_commands():
        do_register(cmd.name, cmd.handler, cmd.help)
        installed.append(cmd.name)
        for alias in cmd.aliases:
            do_register(alias, cmd.handler, cmd.help)
            installed.append(alias)
    return installed

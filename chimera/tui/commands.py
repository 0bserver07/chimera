"""Slash-command registry for the Chimera TUIs (spec P4: one registry per surface).

One table — :data:`COMMAND_DEFS` — is the single source of *built-in*
commands: what exists (name + aliases + argument hint + description), where
it applies (single-lane vs multi-lane surface, the #172 split), what
autocomplete offers (:func:`completion_catalog`, the registry half of
R-IN-2), and what ``/help`` prints (:func:`help_lines` — a commands section
from this registry plus a keys section rendering the *currently bound* keys
via :func:`chimera.tui.keys.key_for`, R-KEY-3).

On top of the static table sits a **dynamic composition layer**: plugin
commands registered in :class:`chimera.plugins.ui.UIExtensionRegistry` (the
same source the REPL installs from) are folded into :func:`commands_for`,
:func:`completion_catalog`, :func:`canonical`, and :func:`help_lines` at call
time, so a hot-swap (``/resync``) that adds or removes a plugin command is
visible the next time a frontend recomputes its catalog. The collision policy
is strict and loud: a plugin command may **never** shadow a built-in — a
name that collides with any built-in name or alias rejects the whole plugin
command, a colliding alias drops just that alias, and every rejection is
reported through :func:`plugin_command_rejections` (built-ins always win).

Built-in dispatch deliberately stays in the frontend
(``MultiplexApp._handle_command``) — for built-ins this module owns the data,
not the handlers. Plugin dispatch lives here
(:func:`dispatch_plugin_command`): the plugin-facing handler contract is the
REPL's ``(session, env, args, out)``, and the TUI adapts to it through a thin
:class:`TUICommandContext` (``say()``, the focused lane's driver, busy state)
passed in the ``session`` position rather than by changing the contract.

Stdlib-only, widget-free, exhaustively unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from chimera.tui.keys import KEY_ACTIONS, ResolvedBinding, display_key, key_for

__all__ = [
    "COMMAND_DEFS",
    "SlashCommand",
    "TUICommandContext",
    "canonical",
    "commands_for",
    "completion_catalog",
    "dispatch_plugin_command",
    "help_lines",
    "plugin_command_defs",
    "plugin_command_rejections",
]


@dataclass(frozen=True)
class SlashCommand:
    """One slash command in the registry.

    Args:
        name: Canonical name, without the leading slash.
        description: One-line ``/help`` text.
        aliases: Alternative names (also completable; dispatch canonicalizes).
        args_hint: Argument placeholder shown in help (e.g. ``"[id]"``).
        context: Where the command exists: ``single`` (one-lane surface),
            ``multi`` (2+ lanes), or ``both``.
        source: ``"builtin"`` for the static table, ``"plugin"`` for entries
            composed from the plugin UI registry at call time.
        plugin: Contributing plugin name, for provenance (plugin entries only).
    """

    name: str
    description: str
    aliases: tuple[str, ...] = ()
    args_hint: str = ""
    context: str = "both"
    source: str = "builtin"
    plugin: str | None = None

    @property
    def slash(self) -> str:
        """The typed form: ``/name``."""
        return "/" + self.name


#: The one table. Alphabetical by name (the completion catalog sorts anyway;
#: keeping the source sorted keeps diffs reviewable).
COMMAND_DEFS: tuple[SlashCommand, ...] = (
    SlashCommand("broadcast", "route new input to every lane", context="multi"),
    SlashCommand("budget", "inspect or set the lane/cohort budget", args_hint="[$0.10/20steps]"),
    SlashCommand("clear", "clear the focused lane's conversation"),
    SlashCommand("cohorts", "pick a saved cohort to resume"),
    SlashCommand("cost", "cumulative cost (per lane when racing)"),
    SlashCommand("exit", "leave the app", aliases=("quit",)),
    SlashCommand("export", "persist the cohort artifact now"),
    SlashCommand("help", "commands and current keybindings"),
    SlashCommand("keys", "effective keybinding table (default/user/migrated)"),
    SlashCommand("model", "model per lane (context window when single)"),
    SlashCommand("resume", "switch to a saved cohort", args_hint="[id]"),
    SlashCommand("results", "open the comparison screen"),
    SlashCommand("resync", "hot-swap plugins/skills/agents from disk"),
    SlashCommand("statusline", "status-line items: id, order, availability"),
    SlashCommand("summary", "cohort scoreboard"),
    SlashCommand("target", "route input to the focused lane only", context="multi"),
    SlashCommand("theme", "pick a theme (live preview) / list / switch",
                 args_hint="[name|list]"),
    SlashCommand("tools", "tools available to the focused lane"),
)

_BY_TOKEN: dict[str, SlashCommand] = {}
for _cmd in COMMAND_DEFS:
    _BY_TOKEN[_cmd.name] = _cmd
    for _alias in _cmd.aliases:
        _BY_TOKEN[_alias] = _cmd
del _cmd


def _applies(cmd: SlashCommand, single: bool | None) -> bool:
    if single is None or cmd.context == "both":
        return True
    return cmd.context == ("single" if single else "multi")


# ---------------------------------------------------------------------------
# Dynamic composition: plugin commands from the shared UI registry
# ---------------------------------------------------------------------------

def _plugin_registry() -> Any:
    """The process-global plugin UI registry (lazy import; core, stdlib-only)."""
    from chimera.plugins.ui import UIExtensionRegistry

    return UIExtensionRegistry


def _compose_plugin_commands(
    registry: Any = None,
) -> tuple[tuple[SlashCommand, ...], tuple[tuple[str, str], ...]]:
    """Fold the plugin UI registry into ``(accepted, rejections)``.

    The collision policy — built-ins win, loudly:

    * A plugin command whose **name** collides with any built-in name or
      alias is rejected whole.
    * A plugin **alias** that collides with a built-in token is dropped
      (the command survives under its clean tokens); the alias is reported.
    * Between plugin commands, tokens are first-come (ascending by name);
      a later claim on a taken token is rejected and reported.

    Args:
        registry: A :class:`chimera.plugins.ui.UIExtensionRegistry`-shaped
            source (injectable for tests). ``None`` uses the global one.

    Returns:
        ``(accepted, rejections)`` — accepted entries as
        ``source="plugin"`` :class:`SlashCommand` rows, and ``(token,
        reason)`` pairs for every rejected token.
    """
    reg = registry if registry is not None else _plugin_registry()
    try:
        contributed = list(reg.get_all_commands())
    except Exception:  # noqa: BLE001 - a broken registry must not kill the catalog
        return (), ()

    accepted: list[SlashCommand] = []
    rejections: list[tuple[str, str]] = []
    claimed: dict[str, str] = {}  # plugin token -> owning plugin command name

    for cmd in contributed:
        name = str(getattr(cmd, "name", "") or "")
        if not name:
            continue
        builtin = _BY_TOKEN.get(name)
        if builtin is not None:
            rejections.append(
                (name, f"shadows the built-in /{builtin.name} — built-ins win")
            )
            continue
        if name in claimed:
            rejections.append(
                (name, f"already provided by plugin command /{claimed[name]}")
            )
            continue
        clean_aliases: list[str] = []
        for alias in tuple(getattr(cmd, "aliases", ()) or ()):
            owner = _BY_TOKEN.get(alias)
            if owner is not None:
                rejections.append((
                    alias,
                    f"alias of plugin command /{name} shadows the built-in "
                    f"/{owner.name} — built-ins win",
                ))
                continue
            if alias in claimed:
                rejections.append(
                    (alias, f"already provided by plugin command /{claimed[alias]}")
                )
                continue
            clean_aliases.append(alias)
        claimed[name] = name
        for alias in clean_aliases:
            claimed[alias] = name
        accepted.append(SlashCommand(
            name=name,
            description=str(getattr(cmd, "help", "") or "(plugin command)"),
            aliases=tuple(clean_aliases),
            context="both",
            source="plugin",
            plugin=getattr(cmd, "plugin", None),
        ))
    return tuple(accepted), tuple(rejections)


def plugin_command_defs(registry: Any = None) -> tuple[SlashCommand, ...]:
    """The accepted plugin commands, as ``source="plugin"`` registry rows.

    Args:
        registry: Plugin UI registry override (tests); ``None`` = global.

    Returns:
        Accepted entries, ascending by name (the registry's own order).
    """
    return _compose_plugin_commands(registry)[0]


def plugin_command_rejections(registry: Any = None) -> tuple[tuple[str, str], ...]:
    """Every plugin token the collision policy rejected, with its reason.

    This is the loud half of the policy: frontends surface these pairs in
    the transcript (at start and after each ``/resync``) so a shadowed
    command never disappears silently.

    Args:
        registry: Plugin UI registry override (tests); ``None`` = global.

    Returns:
        ``(token, reason)`` pairs, in registry order.
    """
    return _compose_plugin_commands(registry)[1]


def commands_for(
    *,
    single: bool | None = None,
    include_plugins: bool = True,
    registry: Any = None,
) -> tuple[SlashCommand, ...]:
    """Registry entries for a surface: built-ins plus accepted plugin commands.

    Args:
        single: True for the one-lane surface, False for multi-lane,
            ``None`` for the unfiltered registry.
        include_plugins: Fold in the plugin UI registry's accepted commands
            (they apply to both surfaces). ``False`` = built-ins only.
        registry: Plugin UI registry override (tests); ``None`` = global.

    Returns:
        The matching commands: built-ins in registry order, then plugin
        entries ascending by name.
    """
    result = [c for c in COMMAND_DEFS if _applies(c, single)]
    if include_plugins:
        result.extend(
            c for c in plugin_command_defs(registry) if _applies(c, single)
        )
    return tuple(result)


def completion_catalog(
    *,
    single: bool | None = None,
    include_plugins: bool = True,
    registry: Any = None,
) -> list[str]:
    """Slash-prefixed completion candidates: names *and* aliases (R-IN-2).

    Aliases complete as themselves (``/q`` still tab-completes to ``/quit``)
    — dispatch canonicalizes, completion does not rewrite what the user
    typed. Accepted plugin commands are included; recompute after a
    ``/resync`` to pick up hot-swapped additions and removals.

    Args:
        single: Surface filter, as in :func:`commands_for`.
        include_plugins: Include accepted plugin commands (default).
        registry: Plugin UI registry override (tests); ``None`` = global.

    Returns:
        Sorted candidate strings for the prompt's autocomplete.
    """
    names: list[str] = []
    for cmd in commands_for(
        single=single, include_plugins=include_plugins, registry=registry
    ):
        names.append(cmd.slash)
        names.extend("/" + a for a in cmd.aliases)
    return sorted(names)


def canonical(
    token: str,
    *,
    include_plugins: bool = True,
    registry: Any = None,
) -> SlashCommand | None:
    """Resolve a typed command token (name or alias) to its registry entry.

    Built-ins resolve first — a plugin token that would collide never
    reaches this point because the composition layer already rejected it.

    Args:
        token: The first word of the input, with or without the leading
            slash (``"/quit"``, ``"quit"``).
        include_plugins: Also resolve accepted plugin commands (default).
        registry: Plugin UI registry override (tests); ``None`` = global.

    Returns:
        The command, or ``None`` for an unknown token.
    """
    bare = token.lstrip("/")
    hit = _BY_TOKEN.get(bare)
    if hit is not None or not include_plugins:
        return hit
    for cmd in plugin_command_defs(registry):
        if bare == cmd.name or bare in cmd.aliases:
            return cmd
    return None


def help_lines(
    *,
    single: bool,
    keymap: Mapping[str, ResolvedBinding] | None = None,
    registry: Any = None,
) -> list[str]:
    """Generate ``/help``: a commands section and a keys section.

    All sections derive from their registries — built-in commands from
    :data:`COMMAND_DEFS` (aliases and argument hints inline), plugin
    commands from the plugin UI registry (with their one-line help and
    provenance; collision-rejected tokens are named so a shadowed command
    never vanishes silently), keys from the keybinding registry rendering
    the *currently bound* key per action (R-KEY-3: after a ``tui.keybinds``
    rebind the help shows the new key; an unbound action is omitted rather
    than lied about).

    Args:
        single: True for the one-lane surface (filters both sections the
            same way the app gates commands and actions).
        keymap: The app's resolved keymap; ``None`` shows defaults.
        registry: Plugin UI registry override (tests); ``None`` = global.

    Returns:
        Display lines (commands, plugin commands when any exist, keys, and
        a fixed composer-keys trailer — the composer's editing keys are not
        yet registry-bound).
    """
    parts: list[str] = []
    for cmd in commands_for(single=single, include_plugins=False):
        entry = cmd.slash
        if cmd.aliases:
            entry += "(" + " ".join("/" + a for a in cmd.aliases) + ")"
        if cmd.args_hint:
            entry += f" {cmd.args_hint}"
        parts.append(entry)
    lines = ["commands: " + " ".join(parts)]

    plugin_defs, rejected = _compose_plugin_commands(registry)
    if plugin_defs:
        plugin_parts: list[str] = []
        for cmd in plugin_defs:
            entry = cmd.slash
            if cmd.aliases:
                entry += "(" + " ".join("/" + a for a in cmd.aliases) + ")"
            entry += f" — {cmd.description}"
            if cmd.plugin:
                entry += f" [{cmd.plugin}]"
            plugin_parts.append(entry)
        lines.append("plugin commands: " + " · ".join(plugin_parts))
    if rejected:
        lines.append(
            "plugin commands rejected (built-ins win): "
            + ", ".join("/" + token for token, _ in rejected)
        )

    hints: list[str] = []
    for action in KEY_ACTIONS:
        if action.context != "global" or not action.show_in_footer:
            continue
        if (action.multi_only and single) or (action.single_only and not single):
            continue
        key = key_for(action.action_id, keymap)
        if not key:
            continue  # unbound by the user: no key to advertise
        hints.append(f"{display_key(key)} {action.description}")
    lines.append("keys: " + " · ".join(hints))
    lines.append("input: Enter submit · Ctrl+J newline · type while running to steer")
    return lines


# ---------------------------------------------------------------------------
# Plugin-command dispatch (the TUI half of the (session, env, args, out) contract)
# ---------------------------------------------------------------------------

def _default_say(msg: str, style: str = "dim") -> None:  # noqa: ARG001 - contract shape
    """No-op transcript sink (placeholder for an unwired context)."""


@dataclass
class TUICommandContext:
    """What the TUI grants a plugin command handler — the ``session`` stand-in.

    Plugin command handlers keep the REPL contract, ``handler(session, env,
    args, out)``; on a TUI surface this context object rides in the
    ``session`` position. It grants exactly what the TUI can honestly give:
    a transcript-writing surface, the **focused lane's** driver, and the
    lane's identity/busy state. REPL-session abilities the TUI does not have
    (a raw ``Session``, its provider, its context object) are simply absent —
    a handler probing with ``getattr(session, "provider", None)`` degrades
    exactly as it does against a bare REPL session, and a handler that
    requires them raises, which the dispatcher reports as a refusal instead
    of half-running.

    Attributes:
        driver: The focused lane's driver (an
            :class:`~chimera.assembly.driver.AgentDriver` or an external
            driver) — model/tools/cost/history live here.
        say: Transcript writer for the focused lane:
            ``say(msg, style="dim")``. The ``out`` callable handlers receive
            writes through this too.
        lane_id: The focused lane's id.
        lane_label: The focused lane's display label.
        model: The focused lane's model string.
        busy: True while the focused lane has a turn in flight — a handler
            that mutates the driver should check this and refuse.
        single: True on the one-lane surface, False in the multiplexer.
        workdir: The focused lane's isolated workspace path (also carried by
            the ``env`` argument), or ``None`` when the lane has none.
        surface: Always ``"tui"`` — lets a handler tell the surfaces apart.
    """

    driver: Any = None
    say: Callable[..., None] = field(default=_default_say)
    lane_id: str = ""
    lane_label: str = ""
    model: str = ""
    busy: bool = False
    single: bool = True
    workdir: str | None = None
    surface: str = "tui"


class _CommandEnv:
    """The ``env`` argument handlers receive: the REPL duck-type (``workdir``)."""

    def __init__(self, workdir: str | None) -> None:
        self.workdir = workdir


def dispatch_plugin_command(
    token: str,
    argline: str,
    context: TUICommandContext,
    *,
    registry: Any = None,
) -> bool:
    """Run an accepted plugin command on a TUI surface, if *token* names one.

    Resolution honors the collision policy: only **accepted** plugin
    commands dispatch, so a plugin command rejected for shadowing a built-in
    can never run here — the built-in branch already handled its token, and
    this function returns ``False`` for it. A handler that raises is
    reported as a refusal in the transcript (clear message, no silent
    half-run) and still counts as handled.

    Args:
        token: The typed command word (``"/greet"`` or ``"greet"``).
        argline: Everything after the command word, whitespace-trimmed.
        context: The :class:`TUICommandContext` for the focused lane.
        registry: Plugin UI registry override (tests); ``None`` = global.

    Returns:
        ``True`` when *token* named an accepted plugin command (even if the
        handler refused or raised); ``False`` when it did not — the caller
        then falls through to its unknown-command message.
    """
    accepted, _ = _compose_plugin_commands(registry)
    bare = token.lstrip("/")
    target: SlashCommand | None = None
    for cmd in accepted:
        if bare == cmd.name or bare in cmd.aliases:
            target = cmd
            break
    if target is None:
        return False

    reg = registry if registry is not None else _plugin_registry()
    ui_cmd = reg.get_command(target.name)
    handler = getattr(ui_cmd, "handler", None)
    if not callable(handler):
        context.say(
            f"plugin command /{target.name} has no callable handler", style="red",
        )
        return True

    env = _CommandEnv(context.workdir)

    def out(msg: Any) -> None:
        context.say(str(msg))

    try:
        handler(context, env, str(argline), out)
    except Exception as exc:  # noqa: BLE001 - refusal beats a crashed frontend
        why = str(exc) or type(exc).__name__
        context.say(f"plugin command /{target.name} refused: {why}", style="red")
    return True
